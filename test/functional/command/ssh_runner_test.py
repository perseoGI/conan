import pytest
import textwrap
from conan.test.utils.tools import TestClient
import os

@pytest.mark.ssh_runner
def test_create_ssh_runner_only_host():
    """
    Tests the ``conan create . `` with ssh runner using only ssh.host
    """
    client = TestClient()
    profile_build = textwrap.dedent(f"""\
    [settings]
    arch={{{{ detect_api.detect_arch() }}}}
    build_type=Release
    compiler=gcc
    compiler.cppstd=gnu17
    compiler.libcxx=libstdc++11
    compiler.version=11
    os=Linux
    """)

    profile_host = textwrap.dedent(f"""\
    [settings]
    arch={{{{ detect_api.detect_arch() }}}}
    build_type=Release
    compiler=gcc
    compiler.cppstd=gnu17
    compiler.libcxx=libstdc++11
    compiler.version=11
    os=Linux
    [runner]
    type=ssh
    ssh.host=localhost
    """)

    client.save({"host": profile_host, "build": profile_build})
    client.run("new cmake_lib -d name=pkg -d version=1.0")
    client.run("create . -pr:h host -pr:b build")

    assert "[100%] Built target example" in client.out
    assert "Restore: pkg/1.0 in pkgc8bc87152b946" in client.out
    assert "Restore: pkg/1.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 in b/pkg8070ba4308584/p" in client.out
    assert "Restore: pkg/1.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 metadata in b/pkg8070ba4308584/d/metadata" in client.out

@pytest.mark.ssh_runner
def test_create_ssh_runner_with_config():
    """
    Tests the ``conan create . ``
    """
    client = TestClient()

    ssh_config = textwrap.dedent(f"""\
    Host local-machine
      HostName localhost
    """)
    client.save({"ssh_config": ssh_config})

    profile_build = textwrap.dedent(f"""\
    [settings]
    arch={{{{ detect_api.detect_arch() }}}}
    build_type=Release
    compiler=gcc
    compiler.cppstd=gnu17
    compiler.libcxx=libstdc++11
    compiler.version=11
    os=Linux
    """)

    profile_host = textwrap.dedent(f"""\
    [settings]
    arch={{{{ detect_api.detect_arch() }}}}
    build_type=Release
    compiler=gcc
    compiler.cppstd=gnu17
    compiler.libcxx=libstdc++11
    compiler.version=11
    os=Linux
    [runner]
    type=ssh
    ssh.host=local-machine
    ssh.configfile={os.path.join(client.current_folder, 'ssh_config')}
    """)

    client.save({"host": profile_host, "build": profile_build})
    client.run("new cmake_lib -d name=pkg -d version=2.0")
    client.run("create . -pr:h host -pr:b build")

    assert "[100%] Built target example" in client.out
    assert "Restore: pkg/2.0 in pkgc6abef0178849" in client.out
    assert "Restore: pkg/2.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 in b/pkgc1542b12b96fb/p" in client.out
    assert "Restore: pkg/2.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 metadata in b/pkgc1542b12b96fb/d/metadata" in client.out


    client.save({".ssh/config": ssh_config}, path='~')
    profile_host = textwrap.dedent(f"""\
    [settings]
    arch={{{{ detect_api.detect_arch() }}}}
    build_type=Release
    compiler=gcc
    compiler.cppstd=gnu17
    compiler.libcxx=libstdc++11
    compiler.version=11
    os=Linux
    [runner]
    type=ssh
    ssh.host=local-machine
    # Let the runner find default config file
    ssh.configfile=True
    """)
    client.save({"host": profile_host})
    client.run("create . -pr:h host -pr:b build")

    assert "[100%] Built target example" in client.out
    assert "Restore: pkg/2.0 in pkgc6abef0178849" in client.out
    assert "Restore: pkg/2.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 in b/pkgc1542b12b96fb/p" in client.out
    assert "Restore: pkg/2.0:746e4557a2789d2071a4b9fb6b4960d7d548ced9 metadata in b/pkgc1542b12b96fb/d/metadata" in client.out
