import subprocess
import pytest
import textwrap
from conan.test.utils.tools import TestClient
from conan.test.assets.cmake import gen_cmakelists
from conan.test.assets.sources import gen_function_h, gen_function_cpp
import sys

@pytest.mark.ssh_runner
def test_create_ssh_runner():
    """
    Tests the ``conan create . ``
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
    # ssh.configfile=True
    """)

    client.save({"host": profile_host, "build": profile_build})
    client.run("new cmake_lib -d name=pkg -d version=0.2")
    client.run("create . -pr:h host -pr:b build")

    assert "[100%] Built target example" in client.out
