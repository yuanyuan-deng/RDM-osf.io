import requests

from addons.base.serializer import StorageAddonSerializer
from addons.gitlab.api import GitLabClient
from addons.gitlab.exceptions import GitLabError
from website.util import api_url_for

class GitLabSerializer(StorageAddonSerializer):

    addon_short_name = 'gitlab'

    # Include host information with more informative labels / formatting
    def serialize_account(self, external_account):
        ret = super(GitLabSerializer, self).serialize_account(external_account)
        host = external_account.oauth_secret
        ret.update({
            'host': host,
            'host_url': host,
        })

        return ret

    def credentials_are_valid(self, user_settings, client):
        if user_settings:
            external_account = user_settings.external_accounts.first()
            client = client or GitLabClient(external_account=external_account)
            try:
                client.user()
            except (GitLabError, IndexError):
                return False
            except requests.exceptions.MissingSchema as exc:
                # The old client allowed us to use 'gitlab.com' instead of 'http://gitlab.com' this allows us to maintain backwards compatibility
                if 'No schema supplied' in exc.message:
                    external_account.oauth_secret = 'http://{}'.format(external_account.oauth_secret)
                    GitLabClient(external_account=external_account).user()
                    external_account.save()
                else:
                    raise exc
        return True

    def serialized_folder(self, node_settings):
        return {
            'path': node_settings.repo,
            'name': '{0} / {1}'.format(node_settings.user, node_settings.repo),
        }

    @property
    def addon_serialized_urls(self):
        node = self.node_settings.owner

        return {
            'auth': api_url_for('oauth_connect', service_name='GitLab'),
            'importAuth': node.api_url_for('gitlab_import_auth'),
            'files': node.web_url_for('collect_file_trees'),
            'folders': node.api_url_for('gitlab_root_folder'),
            'config': node.api_url_for('gitlab_set_config'),
            'deauthorize': node.api_url_for('gitlab_deauthorize_node'),
            'accounts': node.api_url_for('gitlab_account_list'),
        }
