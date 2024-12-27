import sys
from typing import Optional

from conan.api.output import init_colorama
from conan.api.subapi.cache import CacheAPI
from conan.api.subapi.command import CommandAPI
from conan.api.subapi.local import LocalAPI
from conan.api.subapi.lockfile import LockfileAPI
from conan.api.subapi.workspace import WorkspaceAPI
from conan import conan_version
from conan.api.subapi.config import ConfigAPI
from conan.api.subapi.download import DownloadAPI
from conan.api.subapi.export import ExportAPI
from conan.api.subapi.install import InstallAPI
from conan.api.subapi.graph import GraphAPI
from conan.api.subapi.new import NewAPI
from conan.api.subapi.profiles import ProfilesAPI
from conan.api.subapi.list import ListAPI
from conan.api.subapi.remotes import RemotesAPI
from conan.api.subapi.remove import RemoveAPI
from conan.api.subapi.search import SearchAPI
from conan.api.subapi.upload import UploadAPI
from conans.client.migrations import ClientMigrator
from conan.errors import ConanException
from conan.internal.paths import get_conan_user_home
from conans.model.version_range import validate_conan_version


class ConanAPI:
    def __init__(self, cache_folder: Optional[str]=None):

        version = sys.version_info
        if version.major == 2 or version.minor < 6:
            raise ConanException("Conan needs Python >= 3.6")

        init_colorama(sys.stderr)
        self.workspace = WorkspaceAPI(self)
        self.cache_folder = self.workspace.home_folder() or cache_folder or get_conan_user_home()
        self.home_folder = self.cache_folder  # Lets call it home, deprecate "cache"

        # Migration system
        migrator = ClientMigrator(self.cache_folder, conan_version)
        migrator.migrate()

        self.command = CommandAPI(self)
        self.remotes = RemotesAPI(self)
        # Search recipes by wildcard and packages filtering by configuracion
        self.search = SearchAPI(self)
        # Get latest refs and list refs of recipes and packages
        self.list = ListAPI(self)
        self.profiles = ProfilesAPI(self)
        self.install = InstallAPI(self)
        self.graph = GraphAPI(self)
        self.export = ExportAPI(self)
        self.remove = RemoveAPI(self)
        self.config = ConfigAPI(self)
        self.new = NewAPI(self)
        self.upload = UploadAPI(self)
        self.download = DownloadAPI(self)
        self.cache = CacheAPI(self)
        self.lockfile = LockfileAPI(self)
        self.local = LocalAPI(self)

        required_range_new = self.config.global_conf.get("core:required_conan_version")
        if required_range_new:
            validate_conan_version(required_range_new)
