import argparse
from pathlib import Path
import pathlib
import tempfile

from conan.api.conan_api import ConanAPI
from conan.api.output import Color, ConanOutput
from conan.errors import ConanException

import os
from io import BytesIO
import sys

from conan import conan_version
from conan.tools.scm import Version
from conans.model.profile import Profile

class SSHRunner:
    def __init__(
        self,
        conan_api: ConanAPI,
        command: str,
        host_profile: Profile,
        build_profile: Profile,
        args: argparse.Namespace,
        raw_args: list[str],
    ):
        self.conan_api = conan_api
        self.command = command
        self.host_profile = host_profile
        self.build_profile = build_profile
        self.args = args
        self.raw_args = raw_args

        hostname = self._create_ssh_connection()
        self.logger = SSHOutput(hostname)
        self.logger.status(f"Connected to {hostname}", fg=Color.BRIGHT_MAGENTA)
        self.remote_conn = RemoteConnection(self.client, self.logger)

    def run(self):
        self._ensure_runner_environment()
        self._copy_profiles()
        self._copy_working_conanfile_path()
        self._remote_create()

    def _create_ssh_connection(self) -> str:
        from paramiko.config import SSHConfig
        from paramiko.client import SSHClient

        hostname = self.host_profile.runner.get("host")
        if not hostname:
            raise ConanException("Host not specified in runner configuration")
        if self.host_profile.runner.get('use_ssh_config', False):
            ssh_config_file = Path.home() / ".ssh" / "config"
            if not ssh_config_file.exists():
                raise ConanException(f"SSH config file not found at {ssh_config_file}")
            ssh_config = SSHConfig.from_file(open(ssh_config_file))
            if ssh_config and ssh_config.lookup(hostname):
                hostname = ssh_config.lookup(hostname)['hostname']
        self.client = SSHClient()
        self.client.load_system_host_keys()
        self.client.connect(hostname)
        return hostname

    def _ensure_runner_environment(self):
        # Check python3 is available in remote host
        if self.remote_conn.run_command("python3 --version").success:
            self.remote_python_command = "python3"
        else:
            result = self.remote_conn.run_command("python --version")
            if result.success and "Python 3" in result.stdout:
                self.remote_python_command = "python"
            else:
                self.logger.error("Unable to locate Python 3 executable in remote SSH environment")

        # Determine if remote host is Windows
        result = self.remote_conn.run_command(f'{self.remote_python_command} -c "import os; print(os.name)"')
        if not result.success:
            self.logger.error("Unable to determine remote OS type")
        self.is_remote_windows = result.stdout == "nt"

        # Get remote user home folder
        result = self.remote_conn.run_command(f'{self.remote_python_command} -c "from pathlib import Path; print(Path.home())"')
        if not result.success:
            self.logger.error("Unable to determine remote home user folder")
        home_folder = result.stdout

        # Expected remote paths
        remote_folder = Path(home_folder) / ".conan2remote"
        remote_folder = remote_folder.as_posix().replace("\\", "/")
        self.remote_workspace = remote_folder
        remote_conan_home = Path(home_folder) / ".conan2remote" / "conanhome"
        remote_conan_home = remote_conan_home.as_posix().replace("\\", "/")
        self.remote_conan_home = remote_conan_home
        self.logger.debug(f"Remote workfolder: {remote_folder}")

        # Ensure remote folders exist
        for folder in [remote_folder, remote_conan_home]:
            if not self.remote_conn.run_command(f'{self.remote_python_command} -c "import os; os.makedirs(\'{folder}\', exist_ok=True)"').success:
                self.logger.error(f"Unable to create remote workfolder at {folder}: {result.stderr}")

        # TODO: allow multiple venv given the client side conan version
        requested_conan_version = "dev" if conan_version.pre == "dev" else str(conan_version)

        conan_venv = remote_folder + "/venv"
        if self.is_remote_windows:
            conan_cmd = remote_folder + "/venv/Scripts/conan.exe"
        else:
            conan_cmd = remote_folder + "/venv/bin/conan"

        self.logger.debug(f"Expected remote conan home: {remote_conan_home}")
        self.logger.debug(f"Expected remote conan command: {conan_cmd}")

        # Check if remote Conan executable exists, otherwise invoke pip inside venv
        has_remote_conan = self.remote_conn.check_file_exists(conan_cmd)

        if self.is_remote_windows:
            python_command = remote_folder + "/venv" + "/Scripts" + "/python.exe"
        else:
            python_command = remote_folder + "/venv" + "/bin" + "/python"

        if not has_remote_conan:
            self.logger.debug(f"Creating remote venv")
            result = self.remote_conn.run_command(f"{self.remote_python_command} -m venv {conan_venv}")
            if not result.success:
                self.logger.error(f"Unable to create remote venv: {result.stderr}")
            self._install_conan_remotely(python_command, requested_conan_version)
        else:
            version = self.remote_conn.run_command(f"{conan_cmd} --version").stdout
            remote_conan_version = Version(version[version.rfind(" ")+1:])
            if requested_conan_version == "dev" and remote_conan_version.bump(1) == str(conan_version).replace("-dev", ""):
                pass
            elif remote_conan_version != requested_conan_version:
                self.logger.debug(f"Remote Conan version mismatch: {remote_conan_version} != {requested_conan_version}")
                self._install_conan_remotely(python_command, requested_conan_version)

        self._create_remote_conan_wrapper(remote_conan_home, remote_folder, conan_cmd)

    def _install_conan_remotely(self, python_command: str, version: str):
        self.logger.debug(f"Installing conan version: {version}")
        # Note: this may fail on windows
        result = self.remote_conn.run_command(f"{python_command} -m pip install conan{f'=={version}' if version != 'dev' else ' --upgrade'}")
        if not result.success:
            self.logger.error(f"Unable to install conan in venv: {result.stderr}")

    def _create_remote_conan_wrapper(self, remote_conan_home: str, remote_folder: str, conan_cmd: str):

        # Create conan wrapper with proper environment variables
        remote_env = {
            'CONAN_HOME': remote_conan_home,
            'CONAN_RUNNER_ENVIRONMENT': "1" # This env will prevent conan (running remotely) to start an infinite remote call
        }
        if self.is_remote_windows:
            # Wrapper script with environment variables preset
            env_lines = "\n".join([f"set {k}={v}" for k,v in remote_env.items()])
            conan_wrapper_contents = f"""@echo off\n{env_lines}\n{conan_cmd} %*\n"""
        else:
            env_lines = "\n".join([f"export {k}={v}" for k,v in remote_env.items()])
            conan_wrapper_contents = f"""{env_lines}\n{conan_cmd}\n"""

        self.remote_conan = self.remote_conn.create_remote_script(conan_wrapper_contents, remote_folder + "/conan", self.is_remote_windows)
        conan_config_home = self.remote_conn.run_command(f"{self.remote_conan} config home").stdout
        self.logger.debug(f"Remote conan config home returned: {conan_config_home}")
        if not self.remote_conn.run_command(f"{self.remote_conan} profile detect --force"):
            self.logger.error("Error creating default profile in remote machine")


    def _copy_profiles(self):
        if not self.remote_conan_home:
            raise ConanException("Remote Conan home folder not set")
        remote_profile_path = Path(self.remote_conan_home) / "profiles"
        # If profile path does not exist, create the folder to avoid errors
        if not self.remote_conn.check_file_exists(remote_profile_path.as_posix()):
            self.remote_conn.mkdir(remote_profile_path.as_posix())
        # Iterate over all profiles and copy using sftp
        for profile in set(self.args.profile_host + self.args.profile_build):
            dest_filename = remote_profile_path / profile
            profile_path = self.conan_api.profiles.get_path(profile)
            self.logger.debug(f"Copying profile '{profile}': {profile_path} -> {dest_filename}")
            self.remote_conn.put(profile_path, dest_filename.as_posix())
        if not self.args.profile_host:
            dest_filename = remote_profile_path / "default" # in remote use "default" profile
            default_host_profile = self.conan_api.profiles.get_default_host()
            self.logger.debug(f"Copying default profile: {default_host_profile} -> {dest_filename}")
            self.remote_conn.put(default_host_profile, dest_filename.as_posix())

    def _copy_working_conanfile_path(self):
        resolved_path = Path(self.args.path).resolve()
        if resolved_path.is_file():
            resolved_path = resolved_path.parent

        if not resolved_path.is_dir():
            return ConanException("Error determining conanfile directory")

        # Create temporary destination directory
        temp_dir_create_cmd = f"""{self.remote_python_command} -c "import tempfile; print(tempfile.mkdtemp(dir='{self.remote_workspace}'))"""
        result = self.remote_conn.run_command(temp_dir_create_cmd)
        if not result.success or not result.stdout:
            self.logger.error(f"Unable to create remote temporary directory: {result.stderr}")
        self.remote_create_dir = result.stdout.replace("\\", '/')

        # Copy current folder to destination using sftp
        _Path = pathlib.PureWindowsPath if self.is_remote_windows else pathlib.PurePath
        for root, dirs, files in os.walk(resolved_path.as_posix()):
            relative_root = Path(root).relative_to(resolved_path)
            for dir in dirs:
                dst = _Path(self.remote_create_dir).joinpath(relative_root).joinpath(dir).as_posix()
                self.remote_conn.mkdir(dst)
            for file in files:
                orig = os.path.join(root, file)
                dst = _Path(self.remote_create_dir).joinpath(relative_root).joinpath(file).as_posix()
                self.remote_conn.put(orig, dst)

    def _remote_create(self):
        raw_args = self.raw_args
        raw_args[raw_args.index(self.args.path)] = self.remote_create_dir
        raw_args = " ".join(raw_args)
        raw_args = raw_args.replace("&", '"&"').replace("*", '"*"')

        _Path = pathlib.PureWindowsPath if self.is_remote_windows else pathlib.PurePath
        remote_json_output = _Path(self.remote_create_dir).joinpath("conan_create.json").as_posix()
        conan_create_cmd = f'{self.remote_conan} create {raw_args} --format json > {remote_json_output}'
        script_path = _Path(self.remote_create_dir).joinpath("conan_create").as_posix()
        self.remote_conn.create_remote_script(conan_create_cmd, script_path, self.is_remote_windows)
        self.logger.status(f"Remote command: {conan_create_cmd}", fg=Color.BRIGHT_MAGENTA)

        if self.remote_conn.run_interactive_command(script_path, self.is_remote_windows):
            self._update_local_cache(remote_json_output)

        self.client.close()

    def _update_local_cache(self, json_result: str):
        _Path = pathlib.PureWindowsPath if self.is_remote_windows else pathlib.PurePath
        pkg_list_json = _Path(self.remote_create_dir).joinpath("pkg_list.json").as_posix()
        # List every package (built or cached) because local cache could have been deleted
        pkg_list_command = f"{self.remote_conan} list --graph={json_result} --format=json > {pkg_list_json}"
        _, stdout, _ = self.client.exec_command(pkg_list_command)
        if stdout.channel.recv_exit_status() != 0:
            raise ConanException("Unable to generate remote package list")

        conan_cache_tgz = _Path(self.remote_create_dir).joinpath("cache.tgz").as_posix()
        self.logger.debug("Remote cache tgz: " + conan_cache_tgz)
        cache_save_command = f"{self.remote_conan} cache save --list {pkg_list_json} --file {conan_cache_tgz}"
        _, stdout, _ = self.client.exec_command(cache_save_command)
        if stdout.channel.recv_exit_status() != 0:
            raise ConanException("Unable to save remote conan cache state")

        with tempfile.TemporaryDirectory(delete=False) as tmp:
            local_cache_tgz = os.path.join(tmp, 'cache.tgz')
            self.remote_conn.get(conan_cache_tgz, local_cache_tgz)
            self.logger.debug("Retrieved local cache: " + local_cache_tgz)
            self.conan_api.cache.restore(local_cache_tgz)


class SSHOutput(ConanOutput):
    def __init__(self, hostname: str):
        super().__init__()
        self.hostname = hostname
        self.set_warnings_as_errors(True) # Make log errors blocker

    def _write_message(self, msg, fg=None, bg=None, newline=True):
        super()._write_message(f"===> SSH Runner ({self.hostname}): ", Color.BLACK,
                               Color.BRIGHT_YELLOW, newline=False)
        super()._write_message(msg, fg, bg, newline)


class RemoteConnection:
    from paramiko.client import SSHClient
    def __init__(self, client: SSHClient, logger: SSHOutput):
        self.client = client
        self.logger = logger

    def put(self, src: str, dst: str) -> None:
        try:
            sftp = self.client.open_sftp()
            sftp.put(src, dst)
            sftp.close()
        except IOError as e:
            self.logger.error(f"Unable to copy {src} to {dst}:\n{e}")

    def get(self, src: str, dst: str) -> None:
        try:
            sftp = self.client.open_sftp()
            sftp.get(src, dst)
            sftp.close()
        except IOError as e:
            self.logger.error(f"Unable to copy from remote {src} to {dst}:\n{e}")

    def mkdir(self, folder: str) -> None:
        sftp = self.client.open_sftp()
        sftp.mkdir(folder)
        sftp.close()

    def check_file_exists(self, file: str) -> bool:
        try:
            sftp = self.client.open_sftp()
            sftp.stat(file)
            sftp.close()
            return True
        except FileNotFoundError:
            return False

    def create_remote_script(self, script: str, script_path: str, is_remote_windows: bool) -> str:
        script_path += ".bat" if is_remote_windows else ".sh"
        try:
            sftp = self.client.open_sftp()
            sftp.putfo(BytesIO(script.encode()), script_path)
            sftp.close()
        except Exception as e:
            self.logger.error(f"Unable to create remote script in {script_path}:\n{e}")
        return script_path

    class RunResult:
        def __init__(self, success, stdout, stderr):
            self.success = success
            self.stdout = stdout
            self.stderr = stderr

    def run_command(self, command: str) -> RunResult:
        _, stdout, stderr = self.client.exec_command(command)
        return RemoteConnection.RunResult(stdout.channel.recv_exit_status() == 0,
                                   stdout.read().decode().strip(),
                      stderr.read().decode().strip())

    def run_interactive_command(self, command: str, is_remote_windows: bool) -> bool:
        ''' Run a command in an SSH session.
            When requesting a pseudo-terminal from the server,
            ensure we pass width and height that matches the current
            terminal
            :return: True if the command succeeded
        '''
        channel = self.client.get_transport().open_session()
        if sys.stdout.isatty():
            width, height = os.get_terminal_size()
            channel.get_pty(width=width, height=height)

        channel.exec_command(command)
        stdout = channel.makefile("r")

        first_line = True
        while not stdout.channel.exit_status_ready():
            line = stdout.channel.recv(1024)
            if first_line and is_remote_windows:
                # Avoid clearing and moving the cursor when the remote server is Windows
                # https://github.com/PowerShell/Win32-OpenSSH/issues/1738#issuecomment-789434169
                line = line.replace(b"\x1b[2J\x1b[m\x1b[H",b"")
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            first_line = False

        return stdout.channel.recv_exit_status() == 0
