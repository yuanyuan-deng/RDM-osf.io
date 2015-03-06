import mock
from nose.tools import *  # noqa

import httplib as http
from boto.s3.connection import Bucket  # noqa
from boto.exception import S3ResponseError

from framework.auth import Auth
from tests.base import OsfTestCase
from tests.factories import ProjectFactory, AuthUserFactory

from website.addons.s3.utils import validate_bucket_name
from website.models import Comment

from utils import create_mock_wrapper


class TestS3ViewsConfig(OsfTestCase):

    def setUp(self):

        super(TestS3ViewsConfig, self).setUp()

        self.user = AuthUserFactory()
        self.consolidated_auth = Auth(user=self.user)
        self.auth = ('test', self.user.api_keys[0]._primary_key)
        self.project = ProjectFactory(creator=self.user)

        self.project.add_addon('s3', auth=self.consolidated_auth)
        self.project.creator.add_addon('s3')

        self.user_settings = self.user.get_addon('s3')
        self.user_settings.access_key = 'We-Will-Rock-You'
        self.user_settings.secret_key = 'Idontknowanyqueensongs'
        self.user_settings.save()

        self.node_settings = self.project.get_addon('s3')
        self.node_settings.bucket = 'Sheer-Heart-Attack'
        self.node_settings.user_settings = self.project.creator.get_addon('s3')

        self.node_settings.save()
        self.node_url = '/api/v1/project/{0}/'.format(self.project._id)

    @mock.patch('website.addons.s3.views.config.does_bucket_exist')
    @mock.patch('website.addons.s3.views.config.adjust_cors')
    def test_s3_settings_no_bucket(self, mock_cors, mock_does_bucket_exist):
        mock_does_bucket_exist.return_value = False
        mock_cors.return_value = True
        url = self.project.api_url + 's3/settings/'
        rv = self.app.post_json(url, {}, expect_errors=True, auth=self.user.auth)
        assert_true('trouble' in rv.body)

    @mock.patch('website.addons.s3.views.config.does_bucket_exist')
    @mock.patch('website.addons.s3.api.S3Wrapper.from_addon')
    @mock.patch('website.addons.s3.views.config.adjust_cors')
    def test_s3_set_bucket(self, mock_cors, mock_wrapper, mock_exist):
        wrapper = create_mock_wrapper()
        bucket = mock.create_autospec(Bucket)
        bucket.list = lambda: list()
        wrapper.bucket = bucket
        mock_wrapper.return_value = wrapper
        mock_cors.return_value = True
        mock_exist.return_value = True

        url = self.project.api_url + 's3/settings/'
        self.app.post_json(
            url, {'s3_bucket': 'hammertofall'}, auth=self.user.auth,
        )

        self.project.reload()
        self.node_settings.reload()

        assert_equal(self.node_settings.bucket, 'hammertofall')
        assert_equal(self.project.logs[-1].action, 's3_bucket_linked')

    def test_s3_set_bucket_no_settings(self):

        user = AuthUserFactory()
        self.project.add_contributor(user, save=True)
        url = self.project.api_url + 's3/settings/'
        res = self.app.post_json(
            url, {'s3_bucket': 'hammertofall'}, auth=user.auth,
            expect_errors=True
        )
        assert_equal(res.status_code, http.BAD_REQUEST)

    def test_s3_set_bucket_no_auth(self):

        user = AuthUserFactory()
        user.add_addon('s3')
        self.project.add_contributor(user, save=True)
        url = self.project.api_url + 's3/settings/'
        res = self.app.post_json(
            url, {'s3_bucket': 'hammertofall'}, auth=user.auth,
            expect_errors=True
        )
        assert_equal(res.status_code, http.BAD_REQUEST)

    def test_s3_set_bucket_already_authed(self):

        user = AuthUserFactory()
        user.add_addon('s3')
        user_settings = user.get_addon('s3')
        user_settings.access_key = 'foo'
        user_settings.secret_key = 'bar'
        user_settings.save()
        self.project.add_contributor(user, save=True)
        url = self.project.api_url + 's3/settings/'
        res = self.app.post_json(
            url, {'s3_bucket': 'hammertofall'}, auth=user.auth,
            expect_errors=True
        )
        assert_equal(res.status_code, http.BAD_REQUEST)

    @mock.patch('website.addons.s3.api.S3Wrapper.from_addon')
    def test_s3_set_bucket_changes_comments_visibility(self, mock_wrapper):
        wrapper = create_mock_wrapper()
        bucket = mock.create_autospec(Bucket)
        bucket.name = 'Charlie Bucket and the Chocolate Factory'
        path = 'find_or_create_file.guid'
        obj = mock.Mock()
        obj.name = path
        bucket.list = lambda: [obj]
        wrapper.bucket = bucket
        mock_wrapper.return_value = wrapper

        # Create comments
        guid, _ = self.node_settings.find_or_create_file_guid(path)  # The file is in the bucket
        guid2, _ = self.node_settings.find_or_create_file_guid('different_than_path.txt')  # The file isn't in the bucket
        comment = Comment.create(
            auth=Auth(self.project.creator),
            node=self.project,
            target=guid,
            user=self.project.creator,
            page='files',
            content='anything...',
            root_title=path,
        )
        comment2 = Comment.create(
            auth=Auth(self.project.creator),
            node=self.project,
            target=guid2,
            user=self.project.creator,
            page='files',
            content='anything...',
            root_title='different_than_path.txt',
        )
        comment3 = Comment.create(
            auth=Auth(self.project.creator),
            node=self.project,
            target=comment2,
            user=self.project.creator,
            page='files',
            content='anything...',
            root_title='different_than_path.txt',
        )

        # Set bucket
        url = self.project.api_url + 's3/settings/'
        self.app.post_json(
            url, {'s3_bucket': 'doesntmatterreally'}, auth=self.user.auth,
        )
        self.project.reload()
        self.node_settings.reload()

        # Check comments hidden or unhidden
        url = self.project.api_url_for('list_comments')
        res = self.app.get(url, {
            'page': 'files',
            'target': guid._id,
            'rootId': guid._id
        }, auth=self.user.auth)
        comments = res.json.get('comments')
        assert_equal(len(comments), 1)
        assert_false(comments[0]['isHidden'])
        res2 = self.app.get(url, {
            'page': 'files',
            'target': guid2._id,
            'rootId': guid2._id
        }, auth=self.user.auth)
        comments = res2.json.get('comments')
        assert_equal(len(comments), 1)
        assert_true(comments[0]['isHidden'])
        res3 = self.app.get(url, {
            'page': 'files',
            'target': comment2._id,
            'rootId': guid2._id
        }, auth=self.user.auth)
        comments = res2.json.get('comments')
        assert_equal(len(comments), 1)
        assert_true(comments[0]['isHidden'])

    @mock.patch('website.addons.s3.api.S3Wrapper.from_addon')
    def test_s3_set_bucket_registered(self, mock_from_addon):

        mock_from_addon.return_value = create_mock_wrapper()

        registration = self.project.register_node(
            None, self.consolidated_auth, '', ''
        )

        url = registration.api_url + 's3/settings/'
        res = self.app.post_json(
            url, {'s3_bucket': 'hammertofall'}, auth=self.user.auth,
            expect_errors=True,
        )

        assert_equal(res.status_code, http.BAD_REQUEST)

    @mock.patch('website.addons.s3.views.config.has_access')
    @mock.patch('website.addons.s3.views.config.create_osf_user')
    def test_user_settings(self, mock_user, mock_access):
        mock_access.return_value = True
        mock_user.return_value = (
            'osf-user-12345',
            {
                'access_key_id': 'scout',
                'secret_access_key': 'ssshhhhhhhhh'
            }
        )
        url = '/api/v1/settings/s3/'
        self.app.post_json(
            url,
            {
                'access_key': 'scout',
                'secret_key': 'Atticus'
            },
            auth=self.user.auth
        )
        self.user_settings.reload()
        assert_equals(self.user_settings.access_key, 'scout')

    @mock.patch('website.addons.s3.model.AddonS3UserSettings.remove_iam_user')
    def test_s3_remove_user_settings(self, mock_access):
        mock_access.return_value = True
        self.user_settings.access_key = 'to-kill-a-mocking-bucket'
        self.user_settings.secret_key = 'itsasecret'
        self.user_settings.save()
        url = '/api/v1/settings/s3/'
        self.app.delete(url, auth=self.user.auth)
        self.user_settings.reload()
        assert_equals(self.user_settings.access_key, None)
        assert_equals(self.user_settings.secret_key, None)
        assert_equals(mock_access.call_count, 1)

    @mock.patch('website.addons.s3.model.AddonS3UserSettings.remove_iam_user')
    def test_s3_remove_user_settings_none(self, mock_access):
        self.user_settings.access_key = None
        self.user_settings.secret_key = None
        self.user_settings.save()
        url = '/api/v1/settings/s3/'
        self.app.delete(url, auth=self.user.auth)
        self.user_settings.reload()
        assert_equals(mock_access.call_count, 0)

    @mock.patch('website.addons.s3.views.config.has_access')
    def test_user_settings_no_auth(self, mock_access):
        mock_access.return_value = False
        url = '/api/v1/settings/s3/'
        rv = self.app.post_json(url, {}, auth=self.user.auth, expect_errors=True)
        assert_equals(rv.status_int, http.BAD_REQUEST)

    @mock.patch('website.addons.s3.views.config.has_access')
    @mock.patch('website.addons.s3.views.config.create_osf_user')
    def test_node_settings_no_user_settings(self, mock_user, mock_access):
        self.node_settings.user_settings = None
        self.node_settings.save()
        url = self.node_url + 's3/authorize/'

        mock_access.return_value = True
        mock_user.return_value = (
            'osf-user-12345',
            {
                'access_key_id': 'scout',
                'secret_access_key': 'ssshhhhhhhhh'
            }
        )
        self.app.post_json(url, {'access_key': 'scout', 'secret_key': 'ssshhhhhhhhh'}, auth=self.user.auth)

        self.user_settings.reload()
        assert_equals(self.user_settings.access_key, 'scout')

    def test_node_settings_no_user_settings_ui(self):
        self.node_settings.user_settings.access_key = None
        self.node_settings.user_settings = None
        self.node_settings.save()
        url = self.project.url + 'settings/'
        rv = self.app.get(url, auth=self.user.auth)
        assert_true('<label for="s3Addon">Access Key</label>' in rv.body)

    @mock.patch('website.addons.s3.model.get_bucket_drop_down')
    def test_node_settings_user_settings_ui(self, mock_dropdown):
        mock_dropdown.return_value = ['mybucket']
        url = self.project.url + 'settings/'
        rv = self.app.get(url, auth=self.user.auth)
        assert_true('mybucket' in rv.body)

    @mock.patch('website.addons.s3.model.AddonS3UserSettings.remove_iam_user')
    def test_remove_settings_hides_comments(self, mock_access):
        guid, _ = self.node_settings.find_or_create_file_guid('too_young.ha')
        comment = Comment.create(
            auth=Auth(self.project.creator),
            node=self.project,
            target=guid,
            user=self.project.creator,
            page='files',
            content='anything...',
            root_title='too_young.ha',
        )
        comment2 = Comment.create(
            auth=Auth(self.project.creator),
            node=self.project,
            target=comment,
            user=self.project.creator,
            page='files',
            content='anything...',
            root_title='too_young.ha',
        )
        mock_access.return_value = True
        self.user_settings.access_key = 'some-times-naive'
        self.user_settings.secret_key = 'itsasecret'
        self.user_settings.save()
        url = '/api/v1/settings/s3/'
        self.app.delete(url, auth=self.user.auth)
        self.user_settings.reload()

        url = self.project.api_url_for('list_comments')
        res = self.app.get(url, {
            'page': 'files',
            'target': guid._id,
            'rootId': guid._id
        }, auth=self.project.creator.auth)
        comments = res.json.get('comments')
        assert_equal(len(comments), 1)
        assert_true(comments[0]['isHidden'])
        res = self.app.get(url, {
            'page': 'files',
            'target': comment._id,
            'rootId': guid._id
        }, auth=self.project.creator.auth)
        comments = res.json.get('comments')
        assert_equal(len(comments), 1)
        assert_true(comments[0]['isHidden'])


class TestCreateBucket(OsfTestCase):

    def setUp(self):

        super(TestCreateBucket, self).setUp()

        self.user = AuthUserFactory()
        self.consolidated_auth = Auth(user=self.user)
        self.auth = ('test', self.user.api_keys[0]._primary_key)
        self.project = ProjectFactory(creator=self.user)

        self.project.add_addon('s3', auth=self.consolidated_auth)
        self.project.creator.add_addon('s3')

        self.user_settings = self.user.get_addon('s3')
        self.user_settings.access_key = 'We-Will-Rock-You'
        self.user_settings.secret_key = 'Idontknowanyqueensongs'
        self.user_settings.save()

        self.node_settings = self.project.get_addon('s3')
        self.node_settings.bucket = 'Sheer-Heart-Attack'
        self.node_settings.user_settings = self.project.creator.get_addon('s3')

        self.node_settings.save()

    def test_bad_names(self):
        assert_false(validate_bucket_name('bogus naMe'))
        assert_false(validate_bucket_name(''))
        assert_false(validate_bucket_name('no'))
        assert_false(validate_bucket_name('.cantstartwithp'))
        assert_false(validate_bucket_name('or.endwith.'))
        assert_false(validate_bucket_name('..nodoubles'))
        assert_false(validate_bucket_name('no_unders_in'))

    def test_names(self):
        assert_true(validate_bucket_name('imagoodname'))
        assert_true(validate_bucket_name('still.passing'))
        assert_true(validate_bucket_name('can-have-dashes'))
        assert_true(validate_bucket_name('kinda.name.spaced'))

    @mock.patch('website.addons.s3.views.crud.create_bucket')
    def test_create_bucket_pass(self, mock_make):
        mock_make.return_value = True
        url = "/api/v1/project/{0}/s3/newbucket/".format(self.project._id)
        ret = self.app.post_json(url, {'bucket_name': 'doesntevenmatter'}, auth=self.user.auth, expect_errors=True)

        assert_equals(ret.status_int, http.OK)

    @mock.patch('website.addons.s3.views.crud.create_bucket')
    def test_create_bucket_fail(self, mock_make):
        error = S3ResponseError(418, 'because Im a test')
        error.message = 'This should work'
        mock_make.side_effect = error

        url = "/api/v1/project/{0}/s3/newbucket/".format(self.project._id)
        ret = self.app.post_json(url, {'bucket_name': 'doesntevenmatter'}, auth=self.user.auth, expect_errors=True)

        assert_equals(ret.body, '{"message": "This should work"}')
