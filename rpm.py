import argparse
import yaml

from utils import build_package_database, resolve_dependencies, generate_bazel_rules


def main():
    parser = argparse.ArgumentParser(description="生成Bazel RPM规则")
    parser.add_argument("--packages", nargs="+", required=True)
    parser.add_argument("--rpmtree", required=True)
    args = parser.parse_args()

    with open("repo.yaml") as f:
        repos = yaml.safe_load(f)

    all_packages, provides_map = build_package_database(repos)

    resolved_pkgs = resolve_dependencies(args.packages, all_packages, provides_map)

    generate_bazel_rules(resolved_pkgs, args.rpmtree)

    print("""done! file is in below:
    - out_put/rpm_rules.bzl
    - out_put/rpmtree.bzl""")


if __name__ == "__main__":
    main()
