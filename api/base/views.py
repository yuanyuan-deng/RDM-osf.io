import weakref

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import F
from django.db.models.expressions import RawSQL
from django.http import JsonResponse
from rest_framework import generics
from rest_framework import permissions as drf_permissions
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response

from api.base import permissions as base_permissions
from api.base import utils
from api.base.exceptions import RelationshipPostMakesNoChanges
from api.base.filters import ListFilterMixin
from api.base.parsers import JSONAPIRelationshipParser
from api.base.parsers import JSONAPIRelationshipParserForRegularJSON
from api.base.requests import EmbeddedRequest
from api.base.serializers import LinkedNodesRelationshipSerializer
from api.base.serializers import LinkedRegistrationsRelationshipSerializer
from api.base.throttling import RootAnonThrottle, UserRateThrottle
from api.base.utils import is_bulk_request, get_user_auth
from api.nodes.permissions import ContributorOrPublic
from api.nodes.permissions import ContributorOrPublicForRelationshipPointers
from api.nodes.permissions import ReadOnlyIfRegistration
from api.users.serializers import UserSerializer
from framework.auth.oauth_scopes import CoreScopes
from osf.models.contributor import Contributor, get_contributor_permissions
from website.models import Pointer

CACHE = weakref.WeakKeyDictionary()


class JSONAPIBaseView(generics.GenericAPIView):

    def __init__(self, **kwargs):
        assert getattr(self, 'view_name', None), 'Must specify view_name on view.'
        assert getattr(self, 'view_category', None), 'Must specify view_category on view.'
        self.view_fqn = ':'.join([self.view_category, self.view_name])
        super(JSONAPIBaseView, self).__init__(**kwargs)

    def _get_embed_partial(self, field_name, field):
        """Create a partial function to fetch the values of an embedded field. A basic
        example is to include a Node's children in a single response.

        :param str field_name: Name of field of the view's serializer_class to load
        results for
        :return function object -> dict:
        """
        if getattr(field, 'field', None):
            field = field.field

        def partial(item):
            # resolve must be implemented on the field
            v, view_args, view_kwargs = field.resolve(item, field_name, self.request)
            if not v:
                return None
            if isinstance(self.request._request, EmbeddedRequest):
                request = self.request._request
            else:
                request = EmbeddedRequest(self.request)

            view_kwargs.update({
                'request': request,
                'is_embedded': True
            })

            # Setup a view ourselves to avoid all the junk DRF throws in
            # v is a function that hides everything v.cls is the actual view class
            view = v.cls()
            view.args = view_args
            view.kwargs = view_kwargs
            view.request = request
            view.request.parser_context['kwargs'] = view_kwargs
            view.format_kwarg = view.get_format_suffix(**view_kwargs)

            _cache_key = (v.cls, field_name, view.get_serializer_class(), item)
            if _cache_key in CACHE.setdefault(self.request._request, {}):
                # We already have the result for this embed, return it
                return CACHE[self.request._request][_cache_key]

            # Cache serializers. to_representation of a serializer should NOT augment it's fields so resetting the context
            # should be sufficient for reuse
            if not view.get_serializer_class() in CACHE.setdefault(self.request._request, {}):
                CACHE[self.request._request][view.get_serializer_class()] = view.get_serializer_class()(many=isinstance(view, ListModelMixin))
            ser = CACHE[self.request._request][view.get_serializer_class()]

            try:
                ser._context = view.get_serializer_context()

                if not isinstance(view, ListModelMixin):
                    ret = ser.to_representation(view.get_object())
                else:
                    queryset = view.filter_queryset(view.get_queryset())
                    page = view.paginate_queryset(queryset)

                    ret = ser.to_representation(page or queryset)

                    if page is not None:
                        request.parser_context['view'] = view
                        request.parser_context['kwargs'].pop('request')
                        view.paginator.request = request
                        ret = view.paginator.get_paginated_response(ret).data
            except Exception as e:
                with transaction.atomic():
                    ret = view.handle_exception(e).data

            # Allow request to be gc'd
            ser._context = None

            # Cache our final result
            CACHE[self.request._request][_cache_key] = ret

            return ret

        return partial

    def get_serializer_context(self):
        """Inject request into the serializer context. Additionally, inject partial functions
        (request, object -> embed items) if the query string contains embeds.  Allows
         multiple levels of nesting.
        """
        context = super(JSONAPIBaseView, self).get_serializer_context()
        if self.kwargs.get('is_embedded'):
            embeds = []
        else:
            embeds = self.request.query_params.getlist('embed')

        fields_check = self.serializer_class._declared_fields.copy()

        for field in fields_check:
            if getattr(fields_check[field], 'field', None):
                fields_check[field] = fields_check[field].field

        for field in fields_check:
            if getattr(fields_check[field], 'always_embed', False) and field not in embeds:
                embeds.append(unicode(field))
            if getattr(fields_check[field], 'never_embed', False) and field in embeds:
                embeds.remove(field)
        embeds_partials = {}
        for embed in embeds:
            embed_field = fields_check.get(embed)
            embeds_partials[embed] = self._get_embed_partial(embed, embed_field)

        context.update({
            'enable_esi': (
                utils.is_truthy(self.request.query_params.get('esi', django_settings.ENABLE_ESI)) and
                self.request.accepted_renderer.media_type in django_settings.ESI_MEDIA_TYPES
            ),
            'embed': embeds_partials,
            'envelope': self.request.query_params.get('envelope', 'data'),
        })
        return context


class LinkedNodesRelationship(JSONAPIBaseView, generics.RetrieveUpdateDestroyAPIView, generics.CreateAPIView):
    """ Relationship Endpoint for Linked Node relationships

    Used to set, remove, update and retrieve the ids of the linked nodes attached to this collection. For each id, there
    exists a node link that contains that node.

    ##Actions

    ###Create

        Method:        POST
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_nodes",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       201

    This requires both edit permission on the collection, and for the user that is
    making the request to be able to read the nodes requested. Data can contain any number of
    node identifiers. This will create a node_link for all node_ids in the request that
    do not currently have a corresponding node_link in this collection.

    ###Update

        Method:        PUT || PATCH
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_nodes",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       200

    This requires both edit permission on the collection and for the user that is
    making the request to be able to read the nodes requested. Data can contain any number of
    node identifiers. This will replace the contents of the node_links for this collection with
    the contents of the request. It will delete all node links that don't have a node_id in the data
    array, create node links for the node_ids that don't currently have a node id, and do nothing
    for node_ids that already have a corresponding node_link. This means a update request with
    {"data": []} will remove all node_links in this collection

    ###Destroy

        Method:        DELETE
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_nodes",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       204

    This requires edit permission on the node. This will delete any node_links that have a
    corresponding node_id in the request.
    """
    permission_classes = (
        ContributorOrPublicForRelationshipPointers,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ReadOnlyIfRegistration,
    )

    required_read_scopes = [CoreScopes.NODE_LINKS_READ]
    required_write_scopes = [CoreScopes.NODE_LINKS_WRITE]

    serializer_class = LinkedNodesRelationshipSerializer
    parser_classes = (JSONAPIRelationshipParser, JSONAPIRelationshipParserForRegularJSON, )

    def get_object(self):
        object = self.get_node(check_object_permissions=False)
        auth = utils.get_user_auth(self.request)
        obj = {'data': [
            pointer for pointer in
            object.linked_nodes.filter(is_deleted=False, type='osf.node')
            if pointer.can_view(auth)
        ], 'self': object}
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_destroy(self, instance):
        data = self.request.data['data']
        auth = utils.get_user_auth(self.request)
        current_pointers = {pointer._id: pointer for pointer in instance['data']}
        collection = instance['self']
        for val in data:
            if val['id'] in current_pointers:
                collection.rm_pointer(current_pointers[val['id']], auth)

    def create(self, *args, **kwargs):
        try:
            ret = super(LinkedNodesRelationship, self).create(*args, **kwargs)
        except RelationshipPostMakesNoChanges:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return ret


class LinkedRegistrationsRelationship(JSONAPIBaseView, generics.RetrieveUpdateDestroyAPIView, generics.CreateAPIView):
    """ Relationship Endpoint for Linked Registrations relationships

    Used to set, remove, update and retrieve the ids of the linked registrations attached to this collection. For each id, there
    exists a node link that contains that registration.

    ##Actions

    ###Create

        Method:        POST
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_registrations",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       201

    This requires both edit permission on the collection, and for the user that is
    making the request to be able to read the registrations requested. Data can contain any number of
    node identifiers. This will create a node_link for all node_ids in the request that
    do not currently have a corresponding node_link in this collection.

    ###Update

        Method:        PUT || PATCH
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_registrations",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       200

    This requires both edit permission on the collection and for the user that is
    making the request to be able to read the registrations requested. Data can contain any number of
    node identifiers. This will replace the contents of the node_links for this collection with
    the contents of the request. It will delete all node links that don't have a node_id in the data
    array, create node links for the node_ids that don't currently have a node id, and do nothing
    for node_ids that already have a corresponding node_link. This means a update request with
    {"data": []} will remove all node_links in this collection

    ###Destroy

        Method:        DELETE
        URL:           /links/self
        Query Params:  <none>
        Body (JSON):   {
                         "data": [{
                           "type": "linked_registrations",   # required
                           "id": <node_id>   # required
                         }]
                       }
        Success:       204

    This requires edit permission on the node. This will delete any node_links that have a
    corresponding node_id in the request.
    """
    permission_classes = (
        ContributorOrPublicForRelationshipPointers,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ReadOnlyIfRegistration,
    )

    required_read_scopes = [CoreScopes.NODE_LINKS_READ]
    required_write_scopes = [CoreScopes.NODE_LINKS_WRITE]

    serializer_class = LinkedRegistrationsRelationshipSerializer
    parser_classes = (JSONAPIRelationshipParser, JSONAPIRelationshipParserForRegularJSON, )

    def get_object(self):
        object = self.get_node(check_object_permissions=False)
        auth = utils.get_user_auth(self.request)
        obj = {'data': [
            pointer for pointer in
            object.linked_nodes.filter(is_deleted=False, type='osf.registration')
            if pointer.can_view(auth)
        ], 'self': object}
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_destroy(self, instance):
        data = self.request.data['data']
        auth = utils.get_user_auth(self.request)
        current_pointers = {pointer.node._id: pointer for pointer in instance['data']}
        collection = instance['self']
        for val in data:
            if val['id'] in current_pointers:
                collection.rm_pointer(current_pointers[val['id']], auth)

    def create(self, *args, **kwargs):
        try:
            ret = super(LinkedRegistrationsRelationship, self).create(*args, **kwargs)
        except RelationshipPostMakesNoChanges:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return ret


@api_view(('GET',))
@throttle_classes([RootAnonThrottle, UserRateThrottle])
def root(request, format=None, **kwargs):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#Base_base_read).
    """
    if request.user and not request.user.is_anonymous():
        user = request.user
        current_user = UserSerializer(user, context={'request': request}).data
    else:
        current_user = None
    kwargs = request.parser_context['kwargs']
    return_val = {
        'meta': {
            'message': 'Welcome to the OSF API.',
            'version': request.version,
            'current_user': current_user,
        },
        'links': {
            'nodes': utils.absolute_reverse('nodes:node-list', kwargs=kwargs),
            'users': utils.absolute_reverse('users:user-list', kwargs=kwargs),
            'collections': utils.absolute_reverse('collections:collection-list', kwargs=kwargs),
            'registrations': utils.absolute_reverse('registrations:registration-list', kwargs=kwargs),
            'institutions': utils.absolute_reverse('institutions:institution-list', kwargs=kwargs),
            'licenses': utils.absolute_reverse('licenses:license-list', kwargs=kwargs),
            'metaschemas': utils.absolute_reverse('metaschemas:metaschema-list', kwargs=kwargs),
            'addons': utils.absolute_reverse('addons:addon-list', kwargs=kwargs),
        }
    }

    if utils.has_admin_scope(request):
        return_val['meta']['admin'] = True

    return Response(return_val)


def error_404(request, format=None, *args, **kwargs):
    return JsonResponse(
        {'errors': [{'detail': 'Not found.'}]},
        status=404,
        content_type='application/vnd.api+json; application/json'
    )


class BaseContributorDetail(JSONAPIBaseView, generics.RetrieveAPIView):

    # overrides RetrieveAPIView
    def get_object(self):
        node = self.get_node()
        user = self.get_user()
        # May raise a permission denied
        self.check_object_permissions(self.request, user)
        try:
            contributor = node.contributor_set.get(user=user)
        except Contributor.DoesNotExist:
            raise NotFound('{} cannot be found in the list of contributors.'.format(user))

        user.permission = get_contributor_permissions(contributor, as_list=False)
        user.bibliographic = contributor.visible
        user.node_id = node._id
        user.index = list(node.get_contributor_order()).index(contributor.id)
        return user


class BaseContributorList(JSONAPIBaseView, generics.ListAPIView, ListFilterMixin):

    def get_default_queryset(self):
        node = self.get_node()

        qs = node._contributors.all() \
            .annotate(
            index=F('contributor___order'),
            bibliographic=F('contributor__visible'),
            node_id=F('contributor__node__guids___id'),
            permission=RawSQL("""
                SELECT
                  CASE WHEN c.admin IS TRUE
                    THEN 'admin'
                    WHEN c.admin IS FALSE and c.write IS TRUE
                    THEN 'write'
                    WHEN c.admin IS FALSE and c.write is FALSE and c.read IS TRUE
                    THEN 'read'
                  END as permission
                FROM osf_contributor AS c WHERE c.user_id = osf_osfuser.id AND c.node_id = %s LIMIT 1
            """, (node.id, ))
        ).order_by('contributor___order')
        return qs

    def get_queryset(self):
        queryset = self.get_queryset_from_request()
        # If bulk request, queryset only contains contributors in request
        if is_bulk_request(self.request):
            contrib_ids = []
            for item in self.request.data:
                try:
                    contrib_ids.append(item['id'].split('-')[1])
                except AttributeError:
                    raise ValidationError('Contributor identifier not provided.')
                except IndexError:
                    raise ValidationError('Contributor identifier incorrectly formatted.')
            queryset[:] = [contrib for contrib in queryset if contrib._id in contrib_ids]
        return queryset


class BaseNodeLinksDetail(JSONAPIBaseView, generics.RetrieveAPIView):
    pass


class BaseNodeLinksList(JSONAPIBaseView, generics.ListAPIView):

    def get_queryset(self):
        auth = get_user_auth(self.request)
        query = self.get_node()\
                .node_relations.select_related('child')\
                .filter(is_node_link=True, child__is_deleted=False)\
                .exclude(child__type='osf.collection')
        return sorted([
            node_link for node_link in query
            if node_link.child.can_view(auth) and not node_link.child.is_retracted
        ], key=lambda node_link: node_link.child.date_modified, reverse=True)


class BaseLinkedList(JSONAPIBaseView, generics.ListAPIView):

    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        ContributorOrPublic,
        ReadOnlyIfRegistration,
        base_permissions.TokenHasScope,
    )

    required_read_scopes = [CoreScopes.NODE_LINKS_READ]
    required_write_scopes = [CoreScopes.NULL]

    # subclass must set
    serializer_class = None
    view_category = None
    view_name = None

    model_class = Pointer

    def get_queryset(self):
        auth = get_user_auth(self.request)

        linked_node_ids = [
            each.id for each in self.get_node().linked_nodes
            .filter(is_deleted=False)
            .exclude(type='osf.collection')
            .order_by('-date_modified')
            if each.can_view(auth)
        ]
        return self.get_node().linked_nodes.filter(id__in=linked_node_ids)
