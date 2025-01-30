import os
import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from conan import conan_version
from conan.test.assets.genconanfile import GenConanfile
from conan.test.utils.test_files import temp_folder
from conan.test.utils.tools import TestClient
from conans.client.migrations import ClientMigrator
from conan.internal.model.version import Version
from conans.util.files import save, load


def _drop_lru_column(db_folder):
    db = os.path.join(db_folder, "cache.sqlite3")
    connection = sqlite3.connect(db, isolation_level=None, timeout=1, check_same_thread=False)
    rec_cols = 'reference, rrev, path, timestamp'
    pkg_cols = 'reference, rrev, pkgid, prev, path, timestamp, build_id'
    try:
        for table in ("recipes", "packages"):
            columns = pkg_cols if table == "packages" else rec_cols
            connection.execute(f"CREATE TABLE {table}_backup AS SELECT {columns} FROM {table};")
            connection.execute(f"DROP TABLE {table};")
            connection.execute(f"ALTER TABLE {table}_backup RENAME TO {table};")
    finally:
        connection.close()


@pytest.mark.parametrize(["plugin_path", "string_replace", "new_string"],
                         [("profile.py", "msvc", "EME_ESE_VC"),
                          ("compatibility/compatibility.py", "conanfile", "conian_file")])
def test_migration_profile_checker_plugin(plugin_path, string_replace, new_string):
    t = TestClient()
    # Any command that checks the package cache generates the DB
    t.run("list")
    assert os.path.exists(os.path.join(t.cache_folder, "p", "cache.sqlite3"))

    profile_plugin_path = os.path.join(t.cache_folder, "extensions", "plugins", plugin_path)
    contents = load(profile_plugin_path)

    # Let's change the version
    version_txt_file_path = os.path.join(t.cache_folder, "version.txt")
    save(version_txt_file_path, "1.0.0")
    db = os.path.join(t.cache_folder, 'p')
    assert os.path.exists(os.path.join(t.cache_folder, "p", "cache.sqlite3"))
    _drop_lru_column(db)

    # Do a modification to the profile plugin without changing the comment
    contents = contents.replace(string_replace, new_string)
    save(profile_plugin_path, contents)

    # Trigger the migrations
    t.run("-v")
    assert "WARN: Running 2.0.14 Cache DB migration to add LRU column" in t.out
    assert f"Migration: Successfully updated {os.path.basename(plugin_path)}" in t.out
    contents = load(profile_plugin_path)
    # Our changes are removed!!!
    assert string_replace in contents
    assert new_string not in contents

    # New client, everything new
    t2 = TestClient()
    # This generates the new plugin file
    t2.run("list")

    # Do a modification to the profile plugin but changing the comment
    profile_plugin_path2 = os.path.join(t2.cache_folder, "extensions", "plugins", plugin_path)
    contents = load(profile_plugin_path2)
    contents = contents.replace(string_replace, new_string)
    contents = contents.replace("This file was generated by Conan", "This file is from ACME corp, "
                                                                    "please don't touch it.")
    save(profile_plugin_path2, contents)

    # Let's change the version
    version_txt_file_path2 = os.path.join(t2.cache_folder, "version.txt")
    save(version_txt_file_path2, "1.0.0")
    db = os.path.join(t2.cache_folder, 'p')
    _drop_lru_column(db)
    # Trigger the migrations
    t2.run("list")
    assert "WARN: Running 2.0.14 Cache DB migration to add LRU column" in t2.out
    assert f"Migration: Successfully updated" not in t2.out
    contents = load(profile_plugin_path2)
    # Our Changes are kept!
    assert "This file is from ACME corp, " in contents
    assert string_replace not in contents
    assert new_string in contents


def test_migration_db_lru():
    t = TestClient()
    storage = temp_folder()
    save(t.paths.global_conf_path, f"core.cache:storage_path={storage}")
    t.save({"conanfile.py": GenConanfile("pkg", "0.1")})
    t.run("create .")
    # Any command generates the profile and compatibility plugin files
    # Let's change the version
    version_txt_file_path = os.path.join(t.cache_folder, "version.txt")
    save(version_txt_file_path, "1.0.0")
    _drop_lru_column(storage)

    # Trigger the migrations
    t.run("list *")
    assert "WARN: Running 2.0.14 Cache DB migration to add LRU column" in t.out
    assert "pkg/0.1" in t.out


def test_back_migrations():
    t = TestClient()

    # add 3 migrations
    for number in (1, 2, 3):
        migration_file = os.path.join(t.cache_folder, "migrations", f"2.100.0_{number}-migrate.py")
        migrate = textwrap.dedent(f"""
            import os
            def migrate(cache_folder):
                os.remove(os.path.join(cache_folder, "file{number}.txt"))
            """)
        save(migration_file, migrate)
        save(os.path.join(t.cache_folder, f"file{number}.txt"), "some content")
        # Some older versions migrations that shouldn't be applied if we downgrade to current
        wrong_migration_file = os.path.join(t.cache_folder, "migrations", f"2.0_{number}-migrate.py")
        save(wrong_migration_file, "this is not python, it would crash")

    # Let's change the old version
    version_txt_file_path = os.path.join(t.cache_folder, "version.txt")
    save(version_txt_file_path, "200.0")
    t.run("-v")  # Fire the backward migration
    assert f"WARN: Downgrading cache from Conan 200.0 to {conan_version}" in t.out
    for number in (1, 2, 3):
        assert f"WARN: Applying downgrade migration 2.100.0_{number}-migrate.py" in t.out
        assert not os.path.exists(os.path.join(t.cache_folder, f"file{number}.txt"))
        migration_file = os.path.join(t.cache_folder, "migrations", f"2.100.0_{number}-migrate.py")
        assert not os.path.exists(migration_file)


def test_back_default_compatibility_migration():
    t = TestClient()
    t.run("-v")  # Fire the backward migration
    migration_file = os.path.join(t.cache_folder, "migrations", "2.4_1-migrate.py")
    assert os.path.exists(migration_file)

    # downgrade from a clean latest conan_version to 2.3.2
    # simulate that we are in 2.3.2 and the old one is latest conan_version
    migrator = ClientMigrator(t.cache_folder, Version("2.3.2"))

    with patch('conan.api.conan_api.ClientMigrator', new=lambda *args, **kwargs: migrator):
        t.run("-v")  # Fire the backward migration
        assert f"WARN: Downgrading cache from Conan {conan_version} to 2.3.2" in t.out


class TestMigrationCppstdCompat:
    def test_migration(self):
        t = TestClient()
        t.run("-v")
        cppstd_compat_path = "extensions/plugins/compatibility/cppstd_compat.py"
        compatibility_path = "extensions/plugins/compatibility/compatibility.py"

        # both files exist and not modified
        t.save_home({"version.txt": "2.11"})
        t.run("-v")
        assert "def cppstd_compat(conanfile)" in t.load_home(compatibility_path)
        assert not os.path.exists(os.path.join(t.cache_folder, cppstd_compat_path))

    def test_cppstd_modified(self):
        t = TestClient()
        t.run("-v")
        cppstd_compat_path = "extensions/plugins/compatibility/cppstd_compat.py"
        compatibility_path = "extensions/plugins/compatibility/compatibility.py"
        # cppstd_compat modified
        t.save_home({"version.txt": "2.11",
                     compatibility_path: "# This file was generated by Conan",
                     cppstd_compat_path: "custom file content"})
        t.run("-v")
        assert t.load_home(cppstd_compat_path) == "custom file content"
        # compatibility not migrated, keeps the old content
        assert "def cppstd_compat(conanfile)" not in t.load_home(compatibility_path)

    def test_compatibility_modified(self):
        t = TestClient()
        t.run("-v")
        cppstd_compat_path = "extensions/plugins/compatibility/cppstd_compat.py"
        compatibility_path = "extensions/plugins/compatibility/compatibility.py"
        t.save_home({"version.txt": "2.11",
                     cppstd_compat_path: "# This file was generated by Conan POTATO",
                     compatibility_path: "Modified file"})
        t.run("-v")
        assert t.load_home(compatibility_path) == "Modified file"
        # not Removed because compatibility was modified
        assert "POTATO" in t.load_home(cppstd_compat_path)
