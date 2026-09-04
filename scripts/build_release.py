"""build_release.py：发布打包驱动（M3 Task 6，spec「安装包」需求）。

一条命令完成：PyInstaller ``--onedir`` 构建 → （可选）代码签名 → Inno Setup
安装包编译 → 产物 SHA-256 清单与发布说明。

用法（仓库根）::

    uv run python scripts/build_release.py
    uv run python scripts/build_release.py --skip-installer

流程与产物（统一输出到 ``dist/``）：

1. 版本号从 ``apps/workbench-desktop/pyproject.toml`` 读取（唯一版本来源），
   经 ``/DAppVersion`` 注入 Inno Setup 脚本（iss 内含回落默认值）；
2. 清空旧的 ``dist/WarehouseWorkbench/`` 后调 PyInstaller 重建（spec：
   ``apps/workbench-desktop/workbench.spec``）；
3. 有证书环境变量时对主程序签名（安装包编译前，进包的即已签名 exe）；
4. 查找 ISCC（Inno Setup 编译器）：PATH → 常见安装目录；找不到或传
   ``--skip-installer`` 时跳过安装包并输出提示（仍对已有产物计算 SHA-256）；
5. 有证书环境时对安装包签名，随后计算最终产物 SHA-256 写入
   ``dist/SHA256SUMS.txt``，签名状态与发布说明写入 ``dist/RELEASE_NOTES.txt``
   （未签名时在说明中明确标注，spec「安装包」要求）。

代码签名（可选；环境变量缺任一项即视为未签名并记录）：

- ``WORKBENCH_SIGNTOOL``      signtool.exe 路径（缺省从 PATH 查找）；
- ``WORKBENCH_SIGN_CERT``     证书 PFX 文件路径；
- ``WORKBENCH_SIGN_PASSWORD`` 证书私钥口令。

退出码：0 = 构建成功（安装包允许按预期跳过）；1 = 构建失败。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "apps" / "workbench-desktop" / "workbench.spec"
ISS_PATH = REPO_ROOT / "scripts" / "installer" / "warehouse-workbench.iss"
#: 版本唯一来源（iss 的 /DAppVersion 与发布说明均从此读取）
VERSION_SOURCE = REPO_ROOT / "apps" / "workbench-desktop" / "pyproject.toml"

#: 发布产物输出目录（SHA256SUMS.txt / RELEASE_NOTES.txt 同目录）
DIST_DIR = REPO_ROOT / "dist"
ONEDIR_NAME = "WarehouseWorkbench"

#: 签名相关环境变量
SIGNTOOL_ENV = "WORKBENCH_SIGNTOOL"
SIGN_CERT_ENV = "WORKBENCH_SIGN_CERT"
SIGN_PASSWORD_ENV = "WORKBENCH_SIGN_PASSWORD"

#: 签名时间戳服务器（RFC 3161）
TIMESTAMP_URL = "http://timestamp.digicert.com"

#: ISCC 查找顺序：PATH → 常见安装目录
ISCC_FALLBACK_PATHS = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]

#: 未签名时的说明文案（写入 RELEASE_NOTES.txt，spec 要求发布说明标注未签名）
UNSIGNED_NOTE = (
    "未签名：本产物未做代码签名（未配置证书环境变量 "
    f"{SIGN_CERT_ENV} / {SIGN_PASSWORD_ENV}，或未找到 signtool）。"
    "首次运行时 Windows SmartScreen 可能提示风险，需用户选择“仍要运行”；"
    "分发前请务必以 SHA256SUMS.txt 的哈希值核验产物完整性。"
)


def read_version() -> str:
    """从 workbench-desktop 的 pyproject 读取版本号。"""
    with VERSION_SOURCE.open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    return str(version)


def run_command(cmd: list[str], *, step: str) -> int:
    """执行子进程并透传输出（失败时由调用方按步骤名定位处理）。"""
    printable = " ".join(str(part) for part in cmd)
    print(f"[{step}] {printable}")
    return subprocess.call([str(part) for part in cmd])


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256（分块读取，适配大体积安装包）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_iscc() -> Path | None:
    """查找 Inno Setup 编译器 ISCC：先 PATH，再常见安装目录。"""
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    for candidate in ISCC_FALLBACK_PATHS:
        if candidate.is_file():
            return candidate
    return None


def signature_env() -> tuple[Path, Path, str] | None:
    """读取签名环境变量；返回 None 表示无证书环境（按未签名处理）。"""
    cert = os.environ.get(SIGN_CERT_ENV, "").strip()
    password = os.environ.get(SIGN_PASSWORD_ENV, "").strip()
    if not cert or not password:
        return None
    tool = os.environ.get(SIGNTOOL_ENV, "").strip() or shutil.which("signtool")
    if not tool:
        print("[sign] 已设置证书变量但未找到 signtool，本次按未签名处理")
        return None
    return Path(tool), Path(cert), password


def sign_file(path: Path, tool: Path, cert: Path, password: str) -> bool:
    """用 signtool 对单个文件签名（SHA-256 摘要 + RFC 3161 时间戳）。

    口令仅传给子进程参数，不回显到控制台与日志。
    """
    cmd = [
        str(tool),
        "sign",
        "/fd",
        "SHA256",
        "/f",
        str(cert),
        "/p",
        password,
        "/tr",
        TIMESTAMP_URL,
        "/td",
        "SHA256",
        str(path),
    ]
    print(f"[sign] {tool} sign /fd SHA256 /f {cert} ... {path.name}")
    return subprocess.call(cmd) == 0


def write_release_notes(
    version: str,
    installer_built: bool,
    signed: bool,
    checksums: list[tuple[str, str]],
) -> Path:
    """写出发布说明 dist/RELEASE_NOTES.txt（未签名时明确标注）。"""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "仓库分析工作台（WarehouseWorkbench）发布产物说明",
        "=" * 48,
        f"版本：{version}",
        f"构建时间：{now}",
        "构建来源：workbench.spec（PyInstaller --onedir）+ warehouse-workbench.iss（Inno Setup）",
        "",
        "产物清单：",
    ]
    if installer_built:
        lines.append(f"- WarehouseWorkbench-Setup-{version}.exe（安装包，Program Files 安装）")
    lines.append(f"- {ONEDIR_NAME}/（--onedir 目录，主程序 {ONEDIR_NAME}.exe 可免安装运行）")
    lines += [
        "",
        f"签名状态：{'已签名（signtool SHA-256 + RFC 3161 时间戳）' if signed else UNSIGNED_NOTE}",
        "",
        "SHA-256（与 SHA256SUMS.txt 一致）：",
    ]
    lines += [f"- {name}: {digest}" for digest, name in checksums]
    lines += [
        "",
        "完整性校验（PowerShell）：",
        f"  Get-FileHash .\\WarehouseWorkbench-Setup-{version}.exe -Algorithm SHA256",
        "",
        "安装与数据目录约定：",
        "- 安装位置：Program Files\\WarehouseWorkbench；",
        ("- 用户数据：%LOCALAPPDATA%\\WarehouseWorkbench（数据库/备份/报告/凭据），"
         "卸载时保留，重装或升级后可继续使用；"),
        "- 升级：直接运行新版本安装包覆盖安装（关闭运行中的工作台后执行）。",
    ]
    notes = DIST_DIR / "RELEASE_NOTES.txt"
    notes.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WarehouseWorkbench 发布打包驱动（PyInstaller + Inno Setup + 签名/校验）"
    )
    parser.add_argument(
        "--skip-installer",
        action="store_true",
        help="跳过 Inno Setup 安装包编译（PATH 无 ISCC 时也会自动跳过）",
    )
    args = parser.parse_args()

    version = read_version()
    onedir = DIST_DIR / ONEDIR_NAME
    main_exe = onedir / f"{ONEDIR_NAME}.exe"
    installer_exe = DIST_DIR / f"WarehouseWorkbench-Setup-{version}.exe"
    print(f"=== build_release：版本 {version}（产物目录 {DIST_DIR}） ===")

    # 1) PyInstaller --onedir：清旧目录后重建，保证产物干净
    if onedir.exists():
        print(f"[clean] 删除旧产物目录：{onedir}")
        shutil.rmtree(onedir)
    if (
        run_command(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_PATH)],
            step="pyinstaller",
        )
        != 0
        or not main_exe.is_file()
    ):
        print(f"[error] PyInstaller 构建失败（未生成 {main_exe}）")
        return 1
    print(f"[pyinstaller] 主程序构建完成：{main_exe}")

    # 2) 主程序签名（进安装包的必须是已签名 exe，故先于 ISCC）
    signing = signature_env()
    signed = signing is not None
    if signing is not None:
        tool, cert, password = signing
        if not sign_file(main_exe, tool, cert, password):
            print("[error] 主程序签名失败（证书/口令/时间戳服务器请检查）")
            return 1

    # 3) Inno Setup 安装包（版本经 /DAppVersion 注入，输出目录经 /O 覆盖为 dist）
    installer_built = False
    if args.skip_installer:
        print("[installer] 已按 --skip-installer 跳过安装包编译")
    else:
        iscc = find_iscc()
        if iscc is None:
            print("[installer] 未找到 ISCC（Inno Setup 编译器）：已跳过安装包编译")
            print("             安装 Inno Setup 6.2+（含 ChineseSimplified.isl）后重跑即可；")
            print("             当前可分发 dist/WarehouseWorkbench/（--onedir 免安装目录）。")
        else:
            if installer_exe.exists():
                installer_exe.unlink()
            rc = run_command(
                [str(iscc), f"/DAppVersion={version}", f"/O{DIST_DIR}", str(ISS_PATH)],
                step="installer",
            )
            if rc != 0 or not installer_exe.is_file():
                print(f"[error] Inno Setup 编译失败（未生成 {installer_exe}）")
                return 1
            installer_built = True
            print(f"[installer] 安装包构建完成：{installer_exe}")

    # 4) 安装包签名（签名改变哈希，故先签名后计算 SHA-256）
    if installer_built and signing is not None:
        tool, cert, password = signing
        if not sign_file(installer_exe, tool, cert, password):
            print("[error] 安装包签名失败（证书/口令/时间戳服务器请检查）")
            return 1

    # 5) SHA-256 清单（安装包 + 主程序）与发布说明
    checksums: list[tuple[str, str]] = [(sha256_file(main_exe), main_exe.name)]
    if installer_built:
        checksums.append((sha256_file(installer_exe), installer_exe.name))
    for digest, name in checksums:
        print(f"[sha256] {name}  {digest}")
    (DIST_DIR / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for digest, name in checksums), encoding="utf-8"
    )
    notes = write_release_notes(version, installer_built, signed, checksums)
    print(f"[notes] 发布说明：{notes}")

    print("=== build_release 完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
