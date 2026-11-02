# -*- coding: utf-8 -*-
"""
o-848 Skin Mesh Transfer - installer
part of o-848 Tools / Copyright (c) 2026 OhashiYa / MIT License

install.mel から呼ばれる。
  1. ツールを全バージョン共通の maya/scripts へコピー
  2. シェルフ「o848」にボタンを追加（同じボタンがあれば置き換え）
  3. シェルフをすぐ保存
Maya.env や userSetup.py は書き換えない。
"""
import importlib
import importlib.util
import os
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

MODULE = "o848_skin_mesh_transfer"
TOOL_NAME = "o-848 Skin Mesh Transfer"
SHELF = "o848"
OVERLAY = "SMT"
COMMAND = "import {0}\n{0}.show_ui()".format(MODULE)


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _copy_script():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.normpath(os.path.join(here, "..", "scripts", MODULE + ".py"))
    if not os.path.isfile(src):
        raise RuntimeError("Tool file not found: " + src)

    # 全バージョン共通の scripts（例: Documents/maya/scripts）
    dst_dir = os.path.join(cmds.internalVar(userAppDir=True), "scripts")
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, MODULE + ".py")
    shutil.copy2(src, dst)

    if _norm(dst_dir) not in [_norm(p) for p in sys.path]:
        sys.path.append(dst_dir)
    importlib.invalidate_caches()

    # 古い版を読み込み済みなら入れ替える
    if MODULE in sys.modules:
        importlib.reload(sys.modules[MODULE])

    # 別の場所に古いコピーがあり、そちらが優先されていないか確認
    spec = importlib.util.find_spec(MODULE)
    warning = ""
    if spec and spec.origin and _norm(spec.origin) != _norm(dst):
        warning = ("\n\nWarning: another copy is loaded first:\n{}\n"
                   "Please delete it.".format(spec.origin))
    return dst, warning


def _add_shelf_button():
    if cmds.about(batch=True):
        return
    if not cmds.shelfLayout(SHELF, exists=True):
        mel.eval('addNewShelfTab "{}"'.format(SHELF))
    shelf = cmds.shelfLayout(SHELF, q=True, fullPathName=True)

    for child in cmds.shelfLayout(shelf, q=True, childArray=True) or []:
        full = shelf + "|" + child
        if (cmds.shelfButton(full, exists=True)
                and cmds.shelfButton(full, q=True, label=True) == TOOL_NAME):
            cmds.deleteUI(full)

    cmds.shelfButton(parent=shelf, label=TOOL_NAME, annotation=TOOL_NAME,
                     image="pythonFamily.png", imageOverlayLabel=OVERLAY,
                     sourceType="python", command=COMMAND)
    mel.eval("saveAllShelves $gShelfTopLevel")


def install():
    dst, warning = _copy_script()
    _add_shelf_button()
    msg = ("{} has been installed.\n\n"
           "Script: {}\n"
           "Shelf: '{}' tab").format(TOOL_NAME, dst, SHELF) + warning
    print("[{}] Installed: {}".format(TOOL_NAME, dst))
    if not cmds.about(batch=True):
        cmds.confirmDialog(title=TOOL_NAME, message=msg, button=["OK"])


if __name__ == "__main__":
    try:
        install()
    except Exception as e:
        cmds.warning("[{}] Install failed: {}".format(TOOL_NAME, e))
        if not cmds.about(batch=True):
            cmds.confirmDialog(title=TOOL_NAME, message="Install failed:\n" + str(e),
                               button=["OK"])
