from argparse import Namespace
import os
import sys
import json
import platform
import shutil
from typing import Optional, NamedTuple
import yaml
from conan.api.conan_api import ConanAPI
from conan.api.model import ListPattern
from conan.api.output import Color, ConanOutput
from conan.cli import make_abs_path
from conan.internal.runner import RunnerException
from conan.errors import ConanException
from conans.model.profile import Profile
from conans.model.version import Version
from pathlib import Path


class DockerRunner:
    def __init__(self, conan_api: ConanAPI, command: str, host_profile: Profile, build_profile: Profile, args: Namespace, raw_args: list[str]):
        import docker
        import docker.api.build
        try:
            docker_base_urls = [
                None, # Default docker configuration, let the python library detect the socket
                os.environ.get('DOCKER_HOST'), # Connect to socket defined in DOCKER_HOST
                'unix:///var/run/docker.sock', # Default linux socket
                f'unix://{os.path.expanduser("~")}/.rd/docker.sock' # Rancher linux socket
            ]
            for base_url in docker_base_urls:
                try:
                    ConanOutput().verbose(f'Trying to connect to docker "{base_url or "default"}" socket')
                    self.docker_client = docker.DockerClient(base_url=base_url, version='auto')
                    ConanOutput().verbose(f'Connected to docker "{base_url or "default"}" socket')
                    break
                except:
                    continue
            self.docker_api = self.docker_client.api
            docker.api.build.process_dockerfile = lambda dockerfile, path: ('Dockerfile', dockerfile)
        except:
            raise ConanException("Docker Client failed to initialize."
                                 "\n - Check if docker is installed and running"
                                 "\n - Run 'pip install conan[runners]'")
        self.conan_api = conan_api
        self.build_profile = build_profile
        self.args = args
        abs_path = make_abs_path(args.path)
        if abs_path is None:
            raise ConanException("Could not determine absolute path")
        self.abs_host_path = Path(abs_path)
        if args.format:
            raise ConanException("format argument is forbidden if running in a docker runner")

        # Container config
        # https://containers.dev/implementors/json_reference/
        self.configfile = self.config_parser(host_profile.runner.get('configfile'))
        self.dockerfile = host_profile.runner.get('dockerfile') or self.configfile.build.dockerfile
        self.docker_build_context = host_profile.runner.get('build_context') or self.configfile.build.build_context
        self.image = host_profile.runner.get('image') or self.configfile.image
        if not (self.dockerfile or self.image):
            raise ConanException("'dockerfile' or docker image name is needed")
        self.image = self.image or 'conan-runner-default'
        self.name = self.configfile.run.name or f'conan-runner-{host_profile.runner.get("suffix", "docker")}'
        self.remove = str(host_profile.runner.get('remove', 'false')).lower() == 'true'
        self.cache = str(host_profile.runner.get('cache', 'clean'))
        self.container = None

        # Runner config>
        self.abs_runner_home_path = self.abs_host_path / '.conanrunner'
        self.docker_user_name = self.configfile.run.user or 'root'
        self.abs_docker_path = os.path.join(f'/{self.docker_user_name}/conanrunner', os.path.basename(self.abs_host_path)).replace("\\","/")

        # Update conan command and some paths to run inside the container
        raw_args[raw_args.index(args.path)] = self.abs_docker_path
        self.command = ' '.join([f'conan {command}'] + [f'"{raw_arg}"' if ' ' in raw_arg else raw_arg for raw_arg in raw_args] + ['-f json > create.json'])
        self.logger = DockerOutput(self.name)

    def run(self) -> None:
        """
        Run conan inside a Docker container
        """
        self.build_image()
        self.start_container()
        try:
            self.init_container()
            self.run_command(self.command)
            self.update_local_cache()
        except RunnerException as e:
            raise ConanException(f'"{e.command}" inside docker fail'
                                 f'\n\nLast command output: {str(e.stdout_log)}')
        finally:
            if self.container:
                error = sys.exc_info()[0] is not None # Check if error has been raised
                log = self.logger.error if error else self.logger.status
                log('Stopping container')
                self.container.stop()
                if self.remove:
                    log('Removing container')
                    self.container.remove()

    Build = NamedTuple('Build', [('dockerfile', Optional[str]), ('build_context', Optional[str]), ('build_args', Optional[dict]), ('cache_from', Optional[list])])
    Run = NamedTuple('Run', [('name', Optional[str]), ('environment', Optional[dict]), ('user', Optional[str]), ('privileged', Optional[bool]), ('cap_add', Optional[list]), ('security_opt', Optional[list]), ('volumes', Optional[dict]), ('network', Optional[str])])
    Conf = NamedTuple('Conf', [('image', Optional[str]), ('build', Build), ('run', Run)])

    @staticmethod
    def config_parser(file_path: str) -> Conf:
        if file_path:
            def _instans_or_error(value, obj):
                if value and (not isinstance(value, obj)):
                    raise ConanException(f"docker runner configfile syntax error: {value} must be a {obj.__name__}")
                return value
            with open(file_path, 'r') as f:
                runnerfile = yaml.safe_load(f)
            return DockerRunner.Conf(
                image=_instans_or_error(runnerfile.get('image'), str),
                build=DockerRunner.Build(
                    dockerfile=_instans_or_error(runnerfile.get('build', {}).get('dockerfile'), str),
                    build_context=_instans_or_error(runnerfile.get('build', {}).get('build_context'), str),
                    build_args=_instans_or_error(runnerfile.get('build', {}).get('build_args'), dict),
                    cache_from=_instans_or_error(runnerfile.get('build', {}).get('cacheFrom'), list),
                ),
                run=DockerRunner.Run(
                    name=_instans_or_error(runnerfile.get('run', {}).get('name'), str),
                    environment=_instans_or_error(runnerfile.get('run', {}).get('containerEnv'), dict),
                    user=_instans_or_error(runnerfile.get('run', {}).get('containerUser'), str),
                    privileged=_instans_or_error(runnerfile.get('run', {}).get('privileged'), bool),
                    cap_add=_instans_or_error(runnerfile.get('run', {}).get('capAdd'), list),
                    security_opt=_instans_or_error(runnerfile.get('run', {}).get('securityOpt'), list),
                    volumes=_instans_or_error(runnerfile.get('run', {}).get('mounts'), dict),
                    network=_instans_or_error(runnerfile.get('run', {}).get('network'), str),
                )
            )
        else:
            return DockerRunner.Conf(
                image=None,
                build=DockerRunner.Build(dockerfile=None, build_context=None, build_args=None, cache_from=None),
                run=DockerRunner.Run(name=None, environment=None, user=None, privileged=None, cap_add=None,
                        security_opt=None, volumes=None, network=None)
            )

    def build_image(self) -> None:
        if not self.dockerfile:
            return
        self.logger.status(f'Building the Docker image: {self.image}')
        dockerfile_file_path = self.dockerfile
        if os.path.isdir(self.dockerfile):
            dockerfile_file_path = os.path.join(self.dockerfile, 'Dockerfile')
        with open(dockerfile_file_path) as f:
            build_path = self.docker_build_context or os.path.dirname(dockerfile_file_path)
            self.logger.highlight(f"Dockerfile path: '{dockerfile_file_path}'")
            self.logger.highlight(f"Docker build context: '{build_path}'\n")
            docker_build_logs = self.docker_api.build(
                path=build_path,
                dockerfile=f.read(),
                tag=self.image,
                buildargs=self.configfile.build.build_args,
                cache_from=self.configfile.build.cache_from,
            )
        for chunk in docker_build_logs:
            for line in chunk.decode("utf-8").split('\r\n'):
                if line:
                    stream = json.loads(line).get('stream')
                    if stream:
                        ConanOutput().status(stream.strip())

    def start_container(self) -> None:
        volumes, environment = self.create_runner_environment()
        try:
            if self.docker_client.containers.list(all=True, filters={'name': self.name}):
                self.logger.status('Starting the docker container', fg=Color.BRIGHT_MAGENTA)
                self.container = self.docker_client.containers.get(self.name)
                self.container.start()
            else:
                if self.configfile.run.environment:
                    environment.update(self.configfile.run.environment)
                if self.configfile.run.volumes:
                    volumes.update(self.configfile.run.volumes)
                self.logger.status('Creating the docker container', fg=Color.BRIGHT_MAGENTA)
                self.container = self.docker_client.containers.run(
                    self.image,
                    "/bin/bash -c 'while true; do sleep 30; done;'",
                    name=self.name,
                    volumes=volumes,
                    environment=environment,
                    user=self.configfile.run.user,
                    privileged=self.configfile.run.privileged,
                    cap_add=self.configfile.run.cap_add,
                    security_opt=self.configfile.run.security_opt,
                    detach=True,
                    auto_remove=False,
                    network=self.configfile.run.network)
            self.logger.status(f'Container {self.name} running', fg=Color.BRIGHT_MAGENTA)
        except Exception as e:
            raise ConanException(f'Imposible to run the container "{self.name}" with image "{self.image}"'
                                 f'\n\n{str(e)}')

    def run_command(self, command: str, workdir: Optional[str] = None, log: bool = True) -> tuple[str, str]:
        workdir = workdir or self.abs_docker_path
        if log:
            self.logger.status(f'Running in container: "{command}"')
        exec_instance = self.docker_api.exec_create(self.container.id, f"/bin/bash -c '{command}'", workdir=workdir, tty=True)
        exec_output = self.docker_api.exec_start(exec_instance['Id'], tty=True, stream=True, demux=True,)
        stderr_log, stdout_log = '', ''
        try:
            for (stdout_out, stderr_out) in exec_output:
                if stdout_out is not None:
                    stdout_log += stdout_out.decode('utf-8', errors='ignore').strip()
                    if log:
                        ConanOutput().status(stdout_out.decode('utf-8', errors='ignore').strip())
                if stderr_out is not None:
                    stderr_log += stderr_out.decode('utf-8', errors='ignore').strip()
                    if log:
                        ConanOutput().status(stderr_out.decode('utf-8', errors='ignore').strip())
        except Exception as e:
            if platform.system() == 'Windows':
                import pywintypes
                if isinstance(e, pywintypes.error):
                    pass
            else:
                raise e
        exit_metadata = self.docker_api.exec_inspect(exec_instance['Id'])
        if exit_metadata['Running'] or exit_metadata['ExitCode'] > 0:
            raise RunnerException(command=command, stdout_log=stdout_log, stderr_log=stderr_log)
        return stdout_log, stderr_log

    def create_runner_environment(self) -> tuple[dict, dict]:
        shutil.rmtree(self.abs_runner_home_path, ignore_errors=True)
        volumes = {self.abs_host_path: {'bind': self.abs_docker_path, 'mode': 'rw'}}
        environment = {'CONAN_RUNNER_ENVIRONMENT': '1'}

        if self.cache == 'shared':
            volumes[self.conan_api.home_folder] = {'bind': f'/{self.docker_user_name}/.conan2', 'mode': 'rw'}

        if self.cache in ['clean', 'copy']:
            # Copy all conan profiles and config files to docker workspace
            os.mkdir(self.abs_runner_home_path)
            shutil.copytree(
                os.path.join(self.conan_api.home_folder, 'profiles'),
                os.path.join(self.abs_runner_home_path, 'profiles')
            )
            for file_name in ['global.conf', 'settings.yml', 'remotes.json']:
                src_file = os.path.join(self.conan_api.home_folder, file_name)
                if os.path.exists(src_file):
                    shutil.copy(src_file, os.path.join(self.abs_runner_home_path, file_name))

            if self.cache == 'copy':
                tgz_path = os.path.join(self.abs_runner_home_path, 'local_cache_save.tgz')
                self.logger.status(f'Save host cache in: {tgz_path}')
                self.conan_api.cache.save(self.conan_api.list.select(ListPattern("*:*")), tgz_path)
        return volumes, environment

    def init_container(self) -> None:
        min_conan_version = '2.1'
        stdout, _ = self.run_command('conan --version', log=True)
        docker_conan_version = str(stdout.split('Conan version ')[1].replace('\n', '').replace('\r', '')) # Remove all characters and color
        if Version(docker_conan_version) <= Version(min_conan_version):
            raise ConanException(f'conan version inside the container must be greater than {min_conan_version}')
        if self.cache != 'shared':
            self.run_command('mkdir -p ${HOME}/.conan2/profiles', log=False)
            self.run_command('cp -r "'+self.abs_docker_path+'/.conanrunner/profiles/." ${HOME}/.conan2/profiles/.', log=False)
            for file_name in ['global.conf', 'settings.yml', 'remotes.json']:
                if os.path.exists( os.path.join(self.abs_runner_home_path, file_name)):
                    self.run_command('cp "'+self.abs_docker_path+'/.conanrunner/'+file_name+'" ${HOME}/.conan2/'+file_name, log=False)
            if self.cache in ['copy']:
                self.run_command('conan cache restore "'+self.abs_docker_path+'/.conanrunner/local_cache_save.tgz"')

    def update_local_cache(self) -> None:
        if self.cache != 'shared':
            self.run_command('conan list --graph=create.json --graph-binaries=build --format=json > pkglist.json', log=False)
            self.run_command('conan cache save --list=pkglist.json --file "'+self.abs_docker_path+'"/.conanrunner/docker_cache_save.tgz')
            tgz_path = os.path.join(self.abs_runner_home_path, 'docker_cache_save.tgz')
            self.logger.status(f'Restore host cache from: {tgz_path}')
            self.conan_api.cache.restore(tgz_path)

class DockerOutput(ConanOutput):
    def __init__(self, image: str):
        super().__init__()
        self.image = image

    def _write_message(self, msg, fg=None, bg=None, newline=True):
        super()._write_message(f"===> Docker Runner ({self.image}): ", Color.BLACK,
                               Color.BRIGHT_YELLOW, newline=False)
        super()._write_message(msg, fg, bg, newline)
