from api.base import permissions as base_permissions
from api.base.filters import ODMFilterMixin
from api.base.pagination import NoMaxPageSizePagination
from api.base.utils import get_object_or_error
from api.base.views import JSONAPIBaseView
from api.citations.serializers import CitationSerializer
from framework.auth.oauth_scopes import CoreScopes
from rest_framework import permissions as drf_permissions
from rest_framework import generics
from website.models import CitationStyle


class CitationStyleList(JSONAPIBaseView, generics.ListAPIView, ODMFilterMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#Citations_citations_styles_list).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope
    )

    required_read_scopes = [CoreScopes.ALWAYS_PUBLIC]
    required_write_scopes = [CoreScopes.NULL]
    serializer_class = CitationSerializer
    pagination_class = NoMaxPageSizePagination
    view_category = 'citations'
    view_name = 'citation-list'

    # overrides ListAPIView
    def get_default_odm_query(self):
        return

    def get_queryset(self):
        return CitationStyle.find(self.get_query_from_request())

class CitationStyleDetail(JSONAPIBaseView, generics.RetrieveAPIView):
    '''The documentation for this endpoint can be found [here](https://developer.osf.io/#Citations_citations_styles_read).
    '''
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope
    )

    required_read_scopes = [CoreScopes.ALWAYS_PUBLIC]
    required_write_scopes = [CoreScopes.NULL]
    serializer_class = CitationSerializer
    view_category = 'citations'
    view_name = 'citation-detail'

    def get_object(self):
        cit = get_object_or_error(CitationStyle, self.kwargs['citation_id'], prefetch_fields=self.serializer_class().model_field_names)
        self.check_object_permissions(self.request, cit)
        return cit
