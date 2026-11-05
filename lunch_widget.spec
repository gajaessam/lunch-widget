# -*- mode: python ; coding: utf-8 -*-
# 폴더형 빌드: 실행할 때 임시 폴더에 풀지 않아 시작이 빠르고 백신 오탐이 적은 편이다.
# 실행 전 `python packaging/prepare_release.py version-info`로 버전 정보를 만든다.

a = Analysis(
    ['widget.pyw'],
    pathex=['.'],
    datas=[
        ('assets/fonts/*.ttf', 'assets/fonts'),
        ('assets/fonts/*.txt', 'assets/fonts'),
    ],
    excludes=['unittest', 'pydoc', 'test'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LunchWidget',
    console=False,
    upx=False,
    icon='assets/app.ico',
    version='build/version_info.txt',
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name='LunchWidget')
