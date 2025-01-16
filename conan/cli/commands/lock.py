from collections import defaultdict
import os

from conan.api.output import ConanOutput
from conan.cli.command import conan_command, OnceArgument, conan_subcommand

from conan.cli import make_abs_path
from conan.cli.args import common_graph_args, validate_common_graph_args
from conan.cli.printers.graph import print_graph_packages, print_graph_basic
from conan.internal.model.lockfile import Lockfile, LOCKFILE
from conan.internal.model.recipe_ref import RecipeReference
from conan.errors import ConanException
from conan.internal.model.version import Version
from conan.internal.model.version_range import VersionRange


@conan_command(group="Consumer")
def lock(conan_api, parser, *args):  # noqa
    """
    Create or manage lockfiles.
    """


@conan_subcommand()
def lock_create(conan_api, parser, subparser, *args):
    """
    Create a lockfile from a conanfile or a reference.
    """
    common_graph_args(subparser)
    subparser.add_argument("--build-require", action='store_true', default=False,
                           help='Whether the provided reference is a build-require')
    args = parser.parse_args(*args)

    # parameter validation
    validate_common_graph_args(args)

    cwd = os.getcwd()
    path = conan_api.local.get_conanfile_path(args.path, cwd, py=None) if args.path else None
    remotes = conan_api.remotes.list(args.remote) if not args.no_remote else []
    overrides = eval(args.lockfile_overrides) if args.lockfile_overrides else None
    lockfile = conan_api.lockfile.get_lockfile(lockfile=args.lockfile, conanfile_path=path,
                                               cwd=cwd, partial=True, overrides=overrides)
    profile_host, profile_build = conan_api.profiles.get_profiles_from_args(args)

    if path:
        graph = conan_api.graph.load_graph_consumer(path, args.name, args.version,
                                                    args.user, args.channel,
                                                    profile_host, profile_build, lockfile,
                                                    remotes, args.update,
                                                    is_build_require=args.build_require)
    else:
        graph = conan_api.graph.load_graph_requires(args.requires, args.tool_requires,
                                                    profile_host, profile_build, lockfile,
                                                    remotes, args.update)

    print_graph_basic(graph)
    graph.report_graph_error()
    conan_api.graph.analyze_binaries(graph, args.build, remotes=remotes, update=args.update,
                                     lockfile=lockfile)
    print_graph_packages(graph)

    lockfile = conan_api.lockfile.update_lockfile(lockfile, graph, args.lockfile_packages,
                                                  clean=args.lockfile_clean)
    conanfile_path = os.path.dirname(graph.root.path) \
        if graph.root.path and args.lockfile_out is None else cwd
    conan_api.lockfile.save_lockfile(lockfile, args.lockfile_out or "conan.lock", conanfile_path)


@conan_subcommand()
def lock_merge(conan_api, parser, subparser, *args): # noqa
    """
    Merge 2 or more lockfiles.
    """
    subparser.add_argument('--lockfile', action="append", help='Path to lockfile to be merged')
    subparser.add_argument("--lockfile-out", action=OnceArgument, default=LOCKFILE,
                           help="Filename of the created lockfile")

    args = parser.parse_args(*args)

    result = Lockfile()
    for lockfile in args.lockfile:
        lockfile = make_abs_path(lockfile)
        graph_lock = Lockfile.load(lockfile)
        result.merge(graph_lock)

    lockfile_out = make_abs_path(args.lockfile_out)
    result.save(lockfile_out)
    ConanOutput().info("Generated lockfile: %s" % lockfile_out)


@conan_subcommand()
def lock_add(conan_api, parser, subparser, *args):
    """
    Add requires, build-requires or python-requires to an existing or new lockfile.
    The resulting lockfile will be ordered, newer versions/revisions first.
    References can be supplied with and without revisions like "--requires=pkg/version",
    but they must be package references, including at least the version,
    and they cannot contain a version range.
    """
    subparser.add_argument('--requires', action="append", help='Add references to lockfile.')
    subparser.add_argument('--build-requires', action="append",
                           help='Add build-requires to lockfile')
    subparser.add_argument('--python-requires', action="append",
                           help='Add python-requires to lockfile')
    subparser.add_argument('--config-requires', action="append",
                           help='Add config-requires to lockfile')
    subparser.add_argument("--lockfile-out", action=OnceArgument, default=LOCKFILE,
                           help="Filename of the created lockfile")
    subparser.add_argument("--lockfile", action=OnceArgument, help="Filename of the input lockfile")
    args = parser.parse_args(*args)

    lockfile = conan_api.lockfile.get_lockfile(lockfile=args.lockfile, partial=True)

    global_conf = conan_api.config.global_conf
    allow_uppercase = global_conf.get("core:allow_uppercase_pkg_names", check_type=bool)

    def _parse_requires(reqs):
        if reqs:
            result = [RecipeReference.loads(r) for r in reqs]
            [r.validate_ref(allow_uppercase) for r in result]
            return result

    requires = _parse_requires(args.requires)
    build_requires = _parse_requires(args.build_requires)
    python_requires = _parse_requires(args.python_requires)
    config_requires = _parse_requires(args.config_requires)

    lockfile = conan_api.lockfile.add_lockfile(lockfile,
                                               requires=requires,
                                               python_requires=python_requires,
                                               build_requires=build_requires,
                                               config_requires=config_requires)
    conan_api.lockfile.save_lockfile(lockfile, args.lockfile_out)


@conan_subcommand()
def lock_remove(conan_api, parser, subparser, *args):
    """
    Remove requires, build-requires or python-requires from an existing lockfile.
    References can be supplied with and without revisions like "--requires=pkg/version",
    """
    subparser.add_argument('--requires', action="append", help='Remove references to lockfile.')
    subparser.add_argument('--build-requires', action="append",
                           help='Remove build-requires from lockfile')
    subparser.add_argument('--python-requires', action="append",
                           help='Remove python-requires from lockfile')
    subparser.add_argument('--config-requires', action="append",
                           help='Remove config-requires from lockfile')
    subparser.add_argument("--lockfile-out", action=OnceArgument, default=LOCKFILE,
                           help="Filename of the created lockfile")
    subparser.add_argument("--lockfile", action=OnceArgument, help="Filename of the input lockfile")
    args = parser.parse_args(*args)

    lockfile = conan_api.lockfile.get_lockfile(lockfile=args.lockfile, partial=True)
    lockfile = conan_api.lockfile.remove_lockfile(lockfile,
                                                  requires=args.requires,
                                                  python_requires=args.python_requires,
                                                  build_requires=args.build_requires,
                                                  config_requires=args.config_requires)
    conan_api.lockfile.save_lockfile(lockfile, args.lockfile_out)


@conan_subcommand()
def lock_update(conan_api, parser, subparser, *args):
    """
    Update requires, build-requires or python-requires from an existing lockfile.
    References that matches the arguments package names will be replaced by the arguments.
    References can be supplied with and without revisions like "--requires=pkg/version",
    """
    subparser.add_argument('--requires', action="append", help='Update references to lockfile.')
    subparser.add_argument('--build-requires', action="append",
                           help='Update build-requires from lockfile')
    subparser.add_argument('--python-requires', action="append",
                           help='Update python-requires from lockfile')
    subparser.add_argument('--config-requires', action="append",
                           help='Update config-requires from lockfile')
    subparser.add_argument("--lockfile-out", action=OnceArgument, default=LOCKFILE,
                           help="Filename of the created lockfile")
    subparser.add_argument("--lockfile", action=OnceArgument, help="Filename of the input lockfile")
    args = parser.parse_args(*args)

    lockfile = conan_api.lockfile.get_lockfile(lockfile=args.lockfile, partial=True)
    lockfile.update(requires=args.requires, build_requires=args.build_requires,
                    python_requires=args.python_requires, config_requires=args.config_requires)
    conan_api.lockfile.save_lockfile(lockfile, args.lockfile_out)




@conan_subcommand()
def lock_upgrade(conan_api, parser, subparser, *args):
    """
    (Experimental) Upgrade requires, build-requires or python-requires from an existing lockfile given a conanfile
    or a reference.
    """

    """
    TODOs:
    - [ ] args.update should always be True?
    - [x] manage input version ranges
    - [ ] Consider a new option to enable transitive dependencies on different kinds
          (eg. --transitive=all, --transitive=python-requires, ...)
    - [ ] Improve the detection of range versions
    """

    common_graph_args(subparser)
    subparser.add_argument('--update-requires', action="append", help='Update requires from lockfile')
    subparser.add_argument('--update-build-requires', action="append", help='Update build-requires from lockfile')
    subparser.add_argument('--update-python-requires', action="append", help='Update python-requires from lockfile')
    subparser.add_argument('--update-config-requires', action="append", help='Update config-requires from lockfile')
    subparser.add_argument('--build-require', action='store_true', default=False, help='Whether the provided reference is a build-require')
    subparser.add_argument('--transitives', action='store_true', default=False, help='Upgrade also transitive dependencies')
    args = parser.parse_args(*args)

    # parameter validation
    validate_common_graph_args(args)

    if not any([args.update_requires, args.update_build_requires, args.update_python_requires, args.update_config_requires]):
        raise ConanException("At least one of --update-requires, --update-build-requires, "
                             "--update-python-requires or --update-config-requires should be specified")

    cwd = os.getcwd()
    path = conan_api.local.get_conanfile_path(args.path, cwd, py=None) if args.path else None
    remotes = conan_api.remotes.list(args.remote) if not args.no_remote else []
    overrides = eval(args.lockfile_overrides) if args.lockfile_overrides else None
    lockfile = conan_api.lockfile.get_lockfile(lockfile=args.lockfile, conanfile_path=path,
                                               cwd=cwd, partial=True, overrides=overrides)
    profile_host, profile_build = conan_api.profiles.get_profiles_from_args(args)

    def expand_graph():
        if path:
            return conan_api.graph.load_graph_consumer(path, args.name, args.version,
                                                        args.user, args.channel,
                                                        profile_host, profile_build, lockfile,
                                                        remotes, args.update,
                                                        is_build_require=args.build_require)
        return conan_api.graph.load_graph_requires(args.requires, args.tool_requires,
                                                    profile_host, profile_build, lockfile,
                                                    remotes, args.update)
    requested_update = {"requires": args.update_requires or [],
                        "build_requires": args.update_build_requires or [],
                        "python_requires": args.update_python_requires or [],
                        "config_requires": args.update_config_requires or []}
    if args.transitives:
        def is_version_range(ref: str):
            # TODO: use conan's built in
            return ref.find('[') != -1 or ref.find('*') != -1

        def match_version(ref, node):
            if is_version_range(ref):
                return VersionRange(str(RecipeReference.loads(ref).version)).contains(Version(node.ref), resolve_prerelease=True)
            else:
                return node.ref.matches(ref, is_consumer=None)
        updatable_deps = defaultdict(list)
        for node in expand_graph().nodes:
            if not node.ref:
                continue
            for kind, deps in requested_update.items():
                if any(match_version(ref, node) for ref in deps):
                    updatable_deps[kind].append(node.ref.repr_notime())
                    for dep in node.conanfile.dependencies.values():
                        # TODO: dependencies.build.values() ->
                        # dep.context # host or build require (?)
                        updatable_deps[kind].append(dep.ref.repr_notime())
    else:
        updatable_deps = requested_update
    # Remove the lockfile entries that will be updated
    lockfile = conan_api.lockfile.remove_lockfile(lockfile,
                                                  requires=updatable_deps["requires"],
                                                  python_requires=updatable_deps["python_requires"],
                                                  build_requires=updatable_deps["build_requires"],
                                                  config_requires=updatable_deps["config_requires"])
    # Resolve new graph
    graph = expand_graph()
    print_graph_basic(graph)
    graph.report_graph_error()
    conan_api.graph.analyze_binaries(graph, args.build, remotes=remotes, update=args.update,
                                     lockfile=lockfile)
    print_graph_packages(graph)

    lockfile = conan_api.lockfile.update_lockfile(lockfile, graph, args.lockfile_packages,
                                                  clean=args.lockfile_clean)
    conan_api.lockfile.save_lockfile(lockfile, args.lockfile_out or "conan.lock")
