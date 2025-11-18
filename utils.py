import os
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import gzip
import zstandard as zstd
import requests
import hashlib
from collections import deque, defaultdict

os.makedirs("rpm-packages", exist_ok=True)
os.makedirs("out_put", exist_ok=True)


def download_file(url: str, local_path: str) -> bool:
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(response.content)
    except Exception as e:
        print(f"download {url} fail: {str(e)}")
        return False
    return True


def calculate_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        sha256.update(f.read())
    return sha256.hexdigest()


def generate_package_name(pkg_info: dict) -> str:
    #  workspace names may contain only A-Z, a-z, 0-9, '-', '_' and '.'
    epoch = pkg_info.get("epoch", "0")
    ver = pkg_info["ver"].replace("^","-")
    rel = pkg_info["rel"]
    arch = pkg_info["arch"]
    pkg_info['name'] = pkg_info['name'].replace("+","-plus")

    return f"{pkg_info['name']}-{epoch}__{ver}-{rel}.{arch}"


def parse_version_info(version_elem):
    return {
        "epoch": version_elem.get("epoch", "0"),
        "ver": version_elem.get("ver"),
        "rel": version_elem.get("rel"),
    }


def compare_versions(pkg1, pkg2):
    epoch1 = int(pkg1["epoch"])
    epoch2 = int(pkg2["epoch"])

    if epoch1 != epoch2:
        return epoch1 - epoch2

    # 使用简单的字符串比较（实际应使用rpm版本比较算法）
    ver_cmp = (pkg1["ver"] > pkg2["ver"]) - (pkg1["ver"] < pkg2["ver"])
    if ver_cmp != 0:
        return ver_cmp

    return (pkg1["rel"] > pkg2["rel"]) - (pkg1["rel"] < pkg2["rel"])


def parse_package_provides(package, ns_common):
    rpm_ns = "{http://linux.duke.edu/metadata/rpm}"
    provides = []
    format_elem = package.find(f"{ns_common}format")
    if format_elem is not None:
        provides_elem = format_elem.find(f"{rpm_ns}provides")
        if provides_elem is not None:
            for entry in provides_elem.findall(f"{rpm_ns}entry"):
                provides.append(entry.get("name"))
    return provides


def parse_package_requires(package, ns_common):
    rpm_ns = "{http://linux.duke.edu/metadata/rpm}"
    requires = []
    format_elem = package.find(f"{ns_common}format")
    if format_elem is not None:
        requires_elem = format_elem.find(f"{rpm_ns}requires")
        if requires_elem is not None:
            for entry in requires_elem.findall(f"{rpm_ns}entry"):
                requires.append(entry.get("name"))
    return requires


def process_repository(repo):
    # repo_dir = repo['name'].replace('/', '_')
    repo_dir = repo["name"]
    os.makedirs(repo_dir, exist_ok=True)

    # 下载并解析repomd.xml
    repomd_url = urljoin(repo["baseurl"], "repodata/repomd.xml")
    repomd_path = os.path.join(repo_dir, "repomd.xml")
    print(repomd_url)
    if not download_file(repomd_url, repomd_path):
        return None

    # 解析primary元数据位置
    tree = ET.parse(repomd_path)
    root = tree.getroot()
    namespace = "{http://linux.duke.edu/metadata/repo}"
    primary_data = next(
        (
            d
            for d in root.findall(f"{namespace}data")
            if d.attrib.get("type") == "primary"
        ),
        None,
    )
    if primary_data is None:
        return None

    # 下载并解压primary.xml
    primary_href = primary_data.find(f"{namespace}location").attrib["href"]
    primary_url = urljoin(repo["baseurl"], primary_href)
    compressed_path = os.path.join(repo_dir, "primary.xml.compressed")
    if not download_file(primary_url, compressed_path):
        return None

    # 解压文件
    primary_path = os.path.join(repo_dir, "primary.xml")
    if primary_url.endswith(".gz"):
        with (
            gzip.open(compressed_path, "rb") as f_in,
            open(primary_path, "wb") as f_out,
        ):
            f_out.write(f_in.read())
    elif primary_url.endswith(".zst"):
        with open(compressed_path, "rb") as f_in, open(primary_path, "wb") as f_out:
            dctx = zstd.ZstdDecompressor()
            f_out.write(dctx.stream_reader(f_in).read())

    return primary_path


def build_package_database(repos):
    """构建完整的包数据库"""
    all_packages = {}
    provides_map = defaultdict(list)

    for repo in repos:
        try:
            primary_path = process_repository(repo)
            if not primary_path:
                continue

            tree = ET.parse(primary_path)
            root = tree.getroot()
            ns_common = "{http://linux.duke.edu/metadata/common}"

            for pkg in root.findall(f"{ns_common}package"):
                # 解析基本信息
                name = pkg.find(f"{ns_common}name").text
                arch = pkg.find(f"{ns_common}arch").text
                version_elem = pkg.find(f"{ns_common}version")
                location = pkg.find(f"{ns_common}location").attrib["href"]

                # 版本信息
                version_info = parse_version_info(version_elem)
                version_info.update(
                    {
                        "name": name,
                        "arch": arch,
                        "url": urljoin(repo["baseurl"], location),
                    }
                )

                # 解析 checksum（使用XML中的sha256值）
                checksum_elem = pkg.find(f"{ns_common}checksum[@type='sha256']")
                if checksum_elem is not None:
                    version_info["checksum"] = checksum_elem.text

                # 依赖信息
                version_info["provides"] = parse_package_provides(pkg, ns_common)
                version_info["requires"] = parse_package_requires(pkg, ns_common)

                # 生成完整包名
                full_name = generate_package_name(version_info)
                version_info["full_name"] = full_name

                # 保留最新版本
                if (
                    name not in all_packages
                    or compare_versions(version_info, all_packages[name]) > 0
                ):
                    all_packages[name] = version_info

                # 构建提供映射
                for provide in version_info["provides"]:
                    current = provides_map.get(provide)
                    if not current or compare_versions(version_info, current) > 0:
                        provides_map[provide] = version_info

        except Exception as e:
            print(f"处理仓库 {repo['name']} 失败: {str(e)}")

    return all_packages, provides_map


def resolve_dependencies(initial_packages, all_packages, provides_map):
    """递归解析依赖关系"""
    resolved = set()
    queue = deque(initial_packages)
    required = set()

    while queue:
        pkg_name = queue.popleft()
        if pkg_name in resolved:
            continue

        resolved.add(pkg_name)
        if pkg_name not in all_packages:
            continue

        pkg_info = all_packages[pkg_name]

        # 收集所有依赖项
        for req in pkg_info["requires"]:
            required.add(req)
            provider = provides_map.get(req)
            if provider and provider["name"] not in resolved:
                queue.append(provider["name"])

    # 验证所有依赖已满足
    missing = [req for req in required if req not in provides_map]
    if missing:
        print(f"警告：未找到以下依赖的提供者: {'\n'.join(missing)}")
    resolved = sorted(resolved)
    return [all_packages[name] for name in resolved if name in all_packages]


def load_existing_rules(file_path):
    """加载已有的 rpm 规则，返回已存在的包名集合"""
    if not os.path.exists(file_path):
        return set()
    rules = set()
    with open(file_path, "r") as f:
        for line in f:
            if line.startswith("    name = "):
                rule_name = line.split('"')[1]
                rules.add(rule_name)
    return rules


def load_existing_rpmtree(file_path):
    """加载已有的 rpmtree 规则，返回已存在的 rpmtree 名集合"""
    if not os.path.exists(file_path):
        return set()
    rpmtree_names = set()
    with open(file_path, "r") as f:
        for line in f:
            if line.startswith("    name = "):
                rpmtree_name = line.split('"')[1]
                rpmtree_names.add(rpmtree_name)
    return rpmtree_names


def generate_bazel_rules(packages, rpmtree_name):
    """生成Bazel规则，去重后只添加不存在的rpm和rpmtree规则"""
    # 加载已有规则
    existing_rpm_rules = load_existing_rules("out_put/rpm_rules.bzl")
    existing_rpmtree = load_existing_rpmtree("out_put/rpmtree.bzl")

    new_rpm_rules = []
    rpm_targets = []

    for pkg in packages:
        rpm_targets.append(pkg["full_name"])
        if pkg["full_name"] in existing_rpm_rules:
            continue
        # 如果XML中已有checksum，则直接使用，否则下载并计算
        if "checksum" in pkg:
            sha256 = pkg["checksum"]
        else:
            # 下载RPM包并获取SHA256
            local_path = os.path.join("rpm-packages", f"{pkg['full_name']}.rpm")
            if not os.path.exists(local_path):
                if not download_file(pkg["url"], local_path):
                    continue
            sha256 = calculate_sha256(local_path)

        # 生成rpm规则
        rule = f"""rpm(
    name = "{pkg["full_name"]}",
    sha256 = "{sha256}",
    urls = [
       "{pkg["url"]}"
    ],
)"""
        new_rpm_rules.append(rule)
    if new_rpm_rules:
        with open("out_put/rpm_rules.bzl", "a") as f:
            f.write("\n\n".join(new_rpm_rules) + "\n")
    else:
        print("没有新的 rpm 规则需要添加。")

    # 生成rpmtree规则
    new_rpmtree_rule = ""
    if rpmtree_name not in existing_rpmtree:
        formatted_rpm_targets = ""
        if rpm_targets:
            formatted_rpm_targets = (
                ",\n".join([f'"@{name}//rpm"' for name in rpm_targets[:-1]])
                + f',\n"@{rpm_targets[-1]}//rpm",'
            )
            formatted_rpm_targets = formatted_rpm_targets.replace(
                "\n", "\n      ", formatted_rpm_targets.count("\n")
            )
        new_rpmtree_rule = f"""rpmtree(
    name = "{rpmtree_name}",
    rpms = [
      {formatted_rpm_targets}
    ],
    visibility = ["//visibility:public"],
)"""
        with open("out_put/rpmtree.bzl", "a") as f:
            f.write("\n" + new_rpmtree_rule + "\n")
    else:
        print(f"rpmtree {rpmtree_name} 已存在，不再重复添加。")

    return new_rpm_rules, new_rpmtree_rule
