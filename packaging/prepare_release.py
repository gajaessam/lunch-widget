"""릴리스 준비 도구. 버전은 lunch_core.APP_VERSION 한 곳에서만 관리한다.

  python packaging/prepare_release.py check --tag v1.6.0   태그와 버전 일치 확인
  python packaging/prepare_release.py version-info         EXE 버전 정보 생성
  python packaging/prepare_release.py print-version        버전 출력
  python packaging/prepare_release.py package              zip과 latest.json 생성
"""

import argparse
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lunch_core import APP_VERSION  # noqa: E402

BUILD_DIR = os.path.join(ROOT, "build")
RELEASE_DIR = os.path.join(ROOT, "dist", "release")
SETUP_PATH = os.path.join(ROOT, "dist", "installer", "LunchWidget-Setup.exe")
GUIDE_PATH = os.path.join(ROOT, "packaging", "사용법.txt")

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({parts}, 0),
    prodvers=({parts}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('041204B0', [
      StringStruct('FileDescription', '급식 위젯'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'LunchWidget'),
      StringStruct('OriginalFilename', 'LunchWidget.exe'),
      StringStruct('ProductName', '급식 위젯'),
      StringStruct('ProductVersion', '{version}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1042, 1200])])
  ]
)
"""


def check(tag):
    version = tag.lstrip("v").split("-")[0]
    if version != APP_VERSION:
        sys.exit(f"태그 {tag}와 APP_VERSION {APP_VERSION}이 다릅니다.")
    print(f"버전 확인: {APP_VERSION}")


def write_version_info():
    os.makedirs(BUILD_DIR, exist_ok=True)
    numbers = (APP_VERSION.split(".") + ["0", "0"])[:3]
    path = os.path.join(BUILD_DIR, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(parts=", ".join(numbers), version=APP_VERSION))
    print(path)


def package():
    os.makedirs(RELEASE_DIR, exist_ok=True)
    zip_path = os.path.join(RELEASE_DIR, "lunch-widget.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(SETUP_PATH, "급식위젯_설치.exe")
        archive.write(GUIDE_PATH, "사용법.txt")
    with open(os.path.join(RELEASE_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"version": APP_VERSION}, f)
    print(zip_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "version-info", "print-version", "package"))
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    if args.command == "check":
        check(args.tag)
    elif args.command == "version-info":
        write_version_info()
    elif args.command == "print-version":
        print(APP_VERSION)
    else:
        package()


if __name__ == "__main__":
    main()
