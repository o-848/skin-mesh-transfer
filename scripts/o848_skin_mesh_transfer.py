# -*- coding: utf-8 -*-
"""
o-848 Skin Mesh Transfer
part of o-848 Tools
Copyright (c) 2026 OhashiYa
Released under the MIT License (see LICENSE)

対応予定: Maya 2023 - 2026（動作確認済み: Maya 2023, 2026）

スキン付きメッシュを別シーンへ移植するツール。

  1. 元シーン : 選択メッシュを、スキン・骨・マテリアルごと一時ファイル(.ma)へ書き出す
                （リグのコントローラ等を巻き込まないよう、必要なノードだけを厳密に書き出す）
  2. 先シーン : 一時ネームスペースに読み込み
                → スキンを切った形（バインド時の形）でメッシュを複製
                → 先シーンの同名の骨にバインド → ウェイトを転写
                → マテリアルを整理（同名があれば差し替え／競合したものだけ接尾辞）
                → 一時ネームスペースごと削除（読み込んだ骨・元メッシュ等が消える）

使い方:
    import o848_skin_mesh_transfer as smt
    smt.show_ui()
"""
__version__ = "1.1.0"

import os

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

TEMP_NS = "SMT_TMP"
TOOL_NAME = "o-848 Skin Mesh Transfer"
WINDOW = "o848SkinMeshTransferWin"

# UI 設定の保存キー（Maya を再起動しても前回の設定を引き継ぐ）
OPT_MODE = "o848_skinMeshTransfer_saveMode"      # 1=一時フォルダ 2=任意のフォルダ
OPT_FOLDER = "o848_skinMeshTransfer_folder"
OPT_NAME = "o848_skinMeshTransfer_fileName"
OPT_IMPORT_FROM = "o848_skinMeshTransfer_importFrom"  # 1=前回の書き出し先 2=別のファイル
OPT_IMPORT_FILE = "o848_skinMeshTransfer_importFile"
DEFAULT_NAME = "o848_skin_mesh_transfer"
INVALID_CHARS = '\\/:*?"<>|'


# ============================================================
# 名前まわり
# ============================================================
def _leaf(name):
    return name.split("|")[-1]


def _in_temp_ns(name):
    return _leaf(name).startswith(TEMP_NS + ":")


def _strip_temp_ns(name):
    leaf = _leaf(name)
    return leaf[len(TEMP_NS) + 1:] if leaf.startswith(TEMP_NS + ":") else leaf


def _basename(name):
    """パスもネームスペースも取り除いた名前"""
    return _leaf(name).split(":")[-1]


def _unique_name(base, suffix):
    """ルートネームスペースに同名が無ければそのまま、あれば接尾辞を付ける"""
    if not cmds.ls(base):
        return base
    name = base + suffix
    i = 1
    while cmds.ls(name):
        name = "{}{}{}".format(base, suffix, i)
        i += 1
    return name


def _uuid(node):
    return cmds.ls(node, uuid=True)[0]


def _from_uuid(uid):
    return cmds.ls(uid, long=True)[0]


def _deformed_shape(transform):
    for s in cmds.listRelatives(transform, shapes=True, fullPath=True) or []:
        if not cmds.getAttr(s + ".intermediateObject"):
            return s
    return None


def _skin_cluster(mesh):
    sc = mel.eval('findRelatedSkinCluster "{}"'.format(mesh))
    return sc or None


def _resolve_in_scene(name):
    """一時NS内の名前から、先シーンの同名トランスフォーム（骨・親）を探す。
    見つからなければ None、複数あればエラー。"""
    candidates = (
        (_strip_temp_ns(name), False),  # 元のネームスペース付きのまま
        (_basename(name), False),       # ネームスペースなし
        (_basename(name), True),        # どのネームスペースでも
    )
    for pattern, recursive in candidates:
        found = [n for n in (cmds.ls(pattern, long=True, type="transform",
                                     recursive=recursive) or [])
                 if not _in_temp_ns(n)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise RuntimeError("Multiple nodes named: " + pattern)
    return None


# ============================================================
# マテリアルのネットワーク
# ============================================================
def _shading_network(sg):
    nodes = [sg]
    for attr in ("surfaceShader", "displacementShader", "volumeShader"):
        for src in cmds.listConnections("{}.{}".format(sg, attr),
                                        source=True, destination=False) or []:
            nodes += cmds.listHistory(src) or []
    nodes += cmds.listConnections(sg, type="materialInfo") or []
    return list(dict.fromkeys(nodes))


# ============================================================
# 1. 書き出し（元シーン）
# ============================================================
def _collect_skinned_meshes(roots):
    """選択ノードとその子孫から、スキン付きメッシュのトランスフォームを集める。
    グループを選んでも中のメッシュを拾い、スキンの無いメッシュや骨・ロケーターは無視する。"""
    result = []
    for root in cmds.ls(roots, type="transform", long=True) or []:
        candidates = [root] + (cmds.listRelatives(root, allDescendents=True,
                                                  type="transform", fullPath=True) or [])
        for t in candidates:
            if t in result:
                continue
            shape = _deformed_shape(t)
            if shape and cmds.nodeType(shape) == "mesh" and _skin_cluster(t):
                result.append(t)
    return result


def export_meshes(path, meshes=None):
    roots = meshes or cmds.ls(selection=True, long=True)
    if not roots:
        raise RuntimeError("Select skinned meshes or a group.")
    meshes = _collect_skinned_meshes(roots)
    if not meshes:
        raise RuntimeError("No skinned meshes in selection.")

    nodes = set()
    for m in meshes:
        sc = _skin_cluster(m)
        shapes = cmds.listRelatives(m, shapes=True, fullPath=True) or []
        nodes.add(m)
        nodes.update(shapes)
        # 変形ヒストリ（skinCluster, groupParts, tweak など）。骨から先はたどらない
        for s in shapes:
            nodes.update(cmds.ls(cmds.listHistory(s, pruneDagObjects=True) or [],
                                 long=True))
        nodes.update(cmds.ls(cmds.skinCluster(sc, q=True, influence=True), long=True))
        nodes.update(cmds.listConnections(sc, type="dagPose") or [])
        for s in shapes:
            for sg in set(cmds.listConnections(s, type="shadingEngine") or []):
                nodes.update(_shading_network(sg))

    # DAG の祖先も含める（骨の階層を保つため）
    for n in list(nodes):
        if cmds.objExists(n) and cmds.objectType(n, isAType="dagNode"):
            parts = cmds.ls(n, long=True)[0].split("|")[1:]
            for i in range(1, len(parts)):
                nodes.add("|" + "|".join(parts[:i]))

    defaults = set(cmds.ls(defaultNodes=True))
    nodes = [n for n in nodes if cmds.objExists(n) and n not in defaults]

    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    prev_sel = cmds.ls(selection=True, long=True)
    try:
        cmds.select(nodes, replace=True, noExpand=True)
        cmds.file(path, force=True, type="mayaAscii", options="v=0;",
                  preserveReferences=False, exportSelectedStrict=True)
    finally:
        if prev_sel:
            cmds.select(prev_sel, replace=True)
        else:
            cmds.select(clear=True)
    return meshes


# ============================================================
# 2. 読み込み・移植（先シーン）
# ============================================================
PARENT_BY_NAME = "name"          # 元の親と同じ名前のノードの下（無ければワールド）
PARENT_SELECTED = "selected"     # 読み込む前に選択していたノードの下
PARENT_WORLD = "world"           # ワールド直下


def import_meshes(path, suffix="_imp", replace_material=True,
                  parent_mode=PARENT_BY_NAME, copy_bind_matrix=True):
    if not os.path.isfile(path):
        raise RuntimeError("File not found: " + path)

    # 「選択中のノード」は読み込みで選択が変わる前に確定させる
    parent_uuid = None
    if parent_mode == PARENT_SELECTED:
        sel = cmds.ls(selection=True, type="transform", long=True) or []
        if len(sel) != 1:
            raise RuntimeError("Select one parent node.")
        parent_uuid = _uuid(sel[0])

    cmds.namespace(setNamespace=":")
    if cmds.namespace(exists=TEMP_NS):
        cmds.namespace(removeNamespace=TEMP_NS, deleteNamespaceContent=True)

    cmds.file(path, i=True, namespace=TEMP_NS, preserveReferences=False,
              ignoreVersion=True, options="v=0;")

    src_meshes = []
    for shp in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        tr = cmds.listRelatives(shp, parent=True, fullPath=True)[0]
        if _in_temp_ns(tr) and tr not in src_meshes and _skin_cluster(tr):
            src_meshes.append(tr)

    done, errors = [], []
    sg_map = {}  # 読み込んだSG -> 差し替え先の既存SG
    try:
        for src in src_meshes:
            try:
                dup = _transfer_one(src, suffix, parent_mode, parent_uuid,
                                    copy_bind_matrix)
                _handle_materials(dup, suffix, replace_material, sg_map)
                done.append(dup)
            except Exception as e:
                errors.append("{} : {}".format(_strip_temp_ns(src), e))
    finally:
        # 読み込んだ骨・元メッシュ・使わなかったマテリアル等をまとめて削除
        cmds.namespace(setNamespace=":")
        if cmds.namespace(exists=TEMP_NS):
            cmds.namespace(removeNamespace=TEMP_NS, deleteNamespaceContent=True)
    return done, errors


def _transfer_one(src, suffix, parent_mode, parent_uuid, copy_bind_matrix):
    src_sc = _skin_cluster(src)
    src_shape = _deformed_shape(src)
    src_fn = _fn_skin(src_sc)
    src_infs = [p.fullPathName() for p in src_fn.influenceObjects()]

    # 1) 先シーンの骨を名前で探す（足りなければ何も作らずに止める）
    inf_map, missing = {}, []
    for inf in src_infs:
        dst = _resolve_in_scene(inf)
        if dst:
            inf_map[inf] = dst
        else:
            missing.append(_strip_temp_ns(inf))
    if missing:
        raise RuntimeError("Missing joints: " + ", ".join(missing))

    # 2) スキンを切った状態（バインド時の形）で複製
    env = cmds.getAttr(src_sc + ".envelope")
    cmds.setAttr(src_sc + ".envelope", 0)
    try:
        dup = cmds.duplicate(src, returnRootsOnly=True)[0]
    finally:
        cmds.setAttr(src_sc + ".envelope", env)
    uid = _uuid(dup)

    try:
        # 中間シェイプや子ノードを除去
        for c in cmds.listRelatives(_from_uuid(uid), children=True, fullPath=True) or []:
            if cmds.objectType(c, isAType="shape") and not cmds.getAttr(c + ".intermediateObject"):
                continue
            cmds.delete(c)

        # 3) 親：置き場所の設定に従う
        parent = None
        if parent_mode == PARENT_BY_NAME:
            sp = cmds.listRelatives(src, parent=True, fullPath=True)
            if sp:
                try:
                    parent = _resolve_in_scene(sp[0])
                except RuntimeError:
                    parent = None
        elif parent_mode == PARENT_SELECTED:
            if not cmds.ls(parent_uuid):
                raise RuntimeError("Parent node not found.")
            parent = _from_uuid(parent_uuid)
        if parent:
            cmds.parent(_from_uuid(uid), parent)
        elif cmds.listRelatives(_from_uuid(uid), parent=True):
            cmds.parent(_from_uuid(uid), world=True)

        # 4) ルートネームスペースへ改名（競合時のみ接尾辞）
        new_name = _unique_name(_basename(src), suffix)
        cmds.rename(_from_uuid(uid), ":" + new_name)
        cmds.rename(_deformed_shape(_from_uuid(uid)),
                    ":" + _unique_name(new_name + "Shape", suffix))
        dup = _from_uuid(uid)

        # 5) 先シーンの骨にバインド
        dst_infs = [inf_map[i] for i in src_infs]
        joints = [d for d in dst_infs if cmds.nodeType(d) == "joint"]
        others = [d for d in dst_infs if d not in joints]
        if not joints:
            raise RuntimeError("No joint influences.")
        cmds.select(clear=True)
        dst_sc = cmds.skinCluster(
            joints + [dup],
            toSelectedBones=True, bindMethod=0,
            removeUnusedInfluence=False, obeyMaxInfluences=False,
            name=_unique_name(_basename(src_sc), suffix))[0]
        for o in others:
            cmds.skinCluster(dst_sc, edit=True, addInfluence=o, weight=0.0)
        dst_fn = _fn_skin(dst_sc)

        # 6) バインド時の行列を元からコピー（先の骨がポーズ中でも正しく付く）
        if copy_bind_matrix:
            _copy_bind_matrices(src_sc, src_fn, dst_sc, dst_fn, inf_map)

        # 7) ウェイト転写（頂点番号が同一なので番号で完全コピー）
        _copy_weights(src_fn, src_shape, dst_fn, _deformed_shape(dup), inf_map)

        # 8) skinCluster の設定をそろえる
        for attr in ("skinningMethod", "normalizeWeights", "maintainMaxInfluences",
                     "maxInfluences", "deformUserNormals"):
            try:
                cmds.setAttr(dst_sc + "." + attr, cmds.getAttr(src_sc + "." + attr))
            except Exception:
                pass
    except Exception:
        if cmds.ls(uid):
            cmds.delete(_from_uuid(uid))
        raise

    return _from_uuid(uid)


# ============================================================
# スキン（API）
# ============================================================
def _fn_skin(sc):
    sel = om.MSelectionList()
    sel.add(sc)
    return oma.MFnSkinCluster(sel.getDependNode(0))


def _dag_and_all_verts(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    fn = om.MFnSingleIndexedComponent()
    comp = fn.create(om.MFn.kMeshVertComponent)
    fn.setCompleteData(om.MFnMesh(dag).numVertices)
    return dag, comp


def _copy_weights(src_fn, src_shape, dst_fn, dst_shape, inf_map):
    s_dag, s_comp = _dag_and_all_verts(src_shape)
    d_dag, d_comp = _dag_and_all_verts(dst_shape)
    if om.MFnMesh(s_dag).numVertices != om.MFnMesh(d_dag).numVertices:
        raise RuntimeError("Vertex count mismatch.")

    weights, _ = src_fn.getWeights(s_dag, s_comp)
    dst_index = {p.fullPathName(): i for i, p in enumerate(dst_fn.influenceObjects())}
    order = om.MIntArray([dst_index[inf_map[p.fullPathName()]]
                          for p in src_fn.influenceObjects()])
    dst_fn.setWeights(d_dag, d_comp, order, weights, False)

    if cmds.getAttr(src_fn.name() + ".skinningMethod") == 2:  # デュアルクォータニオンのブレンド
        dst_fn.setBlendWeights(d_dag, d_comp, src_fn.getBlendWeights(s_dag, s_comp))


def _copy_bind_matrices(src_sc, src_fn, dst_sc, dst_fn, inf_map):
    for p in src_fn.influenceObjects():
        si = src_fn.indexForInfluenceObject(p)
        sel = om.MSelectionList()
        sel.add(inf_map[p.fullPathName()])
        di = dst_fn.indexForInfluenceObject(sel.getDagPath(0))
        m = cmds.getAttr("{}.bindPreMatrix[{}]".format(src_sc, si))
        cmds.setAttr("{}.bindPreMatrix[{}]".format(dst_sc, di), m, type="matrix")
    cmds.setAttr(dst_sc + ".geomMatrix", cmds.getAttr(src_sc + ".geomMatrix"),
                 type="matrix")


# ============================================================
# マテリアル整理
# ============================================================
def _handle_materials(dup, suffix, replace_material, sg_map):
    shape = _deformed_shape(dup)
    for sg in list(dict.fromkeys(cmds.listConnections(shape, type="shadingEngine") or [])):
        if not _in_temp_ns(sg):
            continue  # 移し終えたSG、または initialShadingGroup など既存のもの
        if sg not in sg_map:
            dst_sg = None
            if replace_material:
                mats = cmds.listConnections(sg + ".surfaceShader",
                                            source=True, destination=False) or []
                if mats:
                    existing = cmds.ls(_basename(mats[0]), materials=True)
                    if existing:
                        dst_sg = _shading_group_of(existing[0])
            if dst_sg is None:
                # 同名マテリアルが無い／接尾辞モード → ネットワークごと移して残す
                _rename_network(sg, suffix)
                continue
            sg_map[sg] = dst_sg
        _reassign(dup, shape, sg, sg_map[sg])


def _shading_group_of(mat):
    for sg in cmds.listConnections(mat, source=False, destination=True,
                                   type="shadingEngine") or []:
        if mat in (cmds.listConnections(sg + ".surfaceShader",
                                        source=True, destination=False) or []):
            return sg
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=mat + "SG")
    cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def _reassign(dup, shape, src_sg, dst_sg):
    targets = {cmds.ls(dup, long=True)[0], cmds.ls(shape, long=True)[0]}
    mine = [m for m in (cmds.sets(src_sg, q=True) or [])
            if set(cmds.ls(m.split(".")[0], long=True)) & targets]
    if mine:
        cmds.sets(mine, edit=True, forceElement=dst_sg)


def _rename_network(sg, suffix):
    for n in _shading_network(sg):
        if cmds.objExists(n) and _in_temp_ns(n):
            cmds.rename(n, ":" + _unique_name(_basename(n), suffix))


# ============================================================
# UI
# ============================================================
def _temp_folder():
    return cmds.internalVar(userTmpDir=True).replace("\\", "/").rstrip("/")


def _opt(key, default):
    return cmds.optionVar(q=key) if cmds.optionVar(exists=key) else default


def _export_path():
    """書き出し先のパス（一時フォルダ or 任意のフォルダ＋ファイル名）"""
    mode = cmds.radioButtonGrp("smt_saveMode", q=True, select=True)
    if mode == 1:
        folder = _temp_folder()
    else:
        folder = cmds.textFieldButtonGrp("smt_folder", q=True, text=True).strip()
        folder = folder.replace("\\", "/").rstrip("/")
        if not folder:
            raise RuntimeError("No folder specified.")

    name = cmds.textFieldGrp("smt_name", q=True, text=True).strip() or DEFAULT_NAME
    if name.lower().endswith(".ma"):
        name = name[:-3]
    bad = [c for c in INVALID_CHARS if c in name]
    if bad:
        raise RuntimeError("Invalid characters in file name: " + " ".join(bad))
    return "{}/{}.ma".format(folder, name)


def _import_path():
    """読み込むファイルのパス（前回の書き出し先 or 別のファイル）"""
    if cmds.radioButtonGrp("smt_importFrom", q=True, select=True) == 1:
        return _export_path()
    path = cmds.textFieldButtonGrp("smt_importFile", q=True, text=True).strip()
    if not path:
        raise RuntimeError("No file specified.")
    return path.replace("\\", "/")


def show_ui():
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    cmds.window(WINDOW, title="{}  v{}".format(TOOL_NAME, __version__),
                widthHeight=(480, 470))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnAttach=("both", 8))

    # ---- 1. 書き出し ----
    cmds.frameLayout(label="1. Export from the source scene", marginWidth=6, marginHeight=6)
    cmds.radioButtonGrp("smt_saveMode", label="Save to", numberOfRadioButtons=2,
                        labelArray2=["Temp folder", "Custom folder"],
                        select=_opt(OPT_MODE, 1), columnWidth3=(90, 120, 140),
                        changeCommand=_refresh)
    cmds.textFieldButtonGrp("smt_folder", label="Folder", buttonLabel="Browse...",
                            text=_opt(OPT_FOLDER, ""), adjustableColumn=2,
                            columnWidth3=(90, 280, 70),
                            buttonCommand=_browse_folder, changeCommand=_refresh)
    cmds.textFieldGrp("smt_name", label="File name", text=_opt(OPT_NAME, DEFAULT_NAME),
                      adjustableColumn=2, columnWidth2=(90, 280),
                      changeCommand=_refresh)
    cmds.text("smt_exportPath", label="", align="left", font="smallPlainLabelFont")
    cmds.button(label="Export Selected Meshes", height=30, command=_on_export)
    cmds.setParent("..")

    # ---- 2. 読み込み ----
    cmds.frameLayout(label="2. Import into the target scene", marginWidth=6, marginHeight=6)
    cmds.radioButtonGrp("smt_importFrom", label="Import from", numberOfRadioButtons=2,
                        labelArray2=["Last export", "Another file"],
                        select=_opt(OPT_IMPORT_FROM, 1), columnWidth3=(90, 120, 140),
                        changeCommand=_refresh)
    cmds.textFieldButtonGrp("smt_importFile", label="File", buttonLabel="Browse...",
                            text=_opt(OPT_IMPORT_FILE, ""), adjustableColumn=2,
                            columnWidth3=(90, 280, 70),
                            buttonCommand=_browse_import_file, changeCommand=_refresh)
    cmds.text("smt_importPath", label="", align="left", font="smallPlainLabelFont")

    cmds.separator(height=8, style="in")
    cmds.radioButtonGrp("smt_mode", label="If a material exists",
                        numberOfRadioButtons=2,
                        labelArray2=["Use the existing one", "Import a copy"],
                        select=1, columnWidth3=(130, 150, 120))
    cmds.radioButtonGrp("smt_parent", label="Place meshes under",
                        numberOfRadioButtons=3,
                        labelArray3=["Matching parent", "Selected node", "World"],
                        select=1, columnWidth4=(130, 120, 100, 60))
    cmds.textFieldGrp("smt_suffix", label="Suffix for name clashes", text="_imp",
                      columnWidth2=(130, 120))
    cmds.checkBoxGrp("smt_opts", numberOfCheckBoxes=1, label="",
                     label1="Keep the original bind pose", value1=True,
                     columnWidth2=(130, 220))
    cmds.button(label="Import and Transfer", height=30, command=_on_import)
    cmds.setParent("..")

    cmds.showWindow(WINDOW)
    _refresh()


def _refresh(*_):
    """欄の有効／無効、パス表示、設定の保存"""
    save_mode = cmds.radioButtonGrp("smt_saveMode", q=True, select=True)
    import_from = cmds.radioButtonGrp("smt_importFrom", q=True, select=True)
    cmds.textFieldButtonGrp("smt_folder", edit=True, enable=(save_mode == 2))
    cmds.textFieldButtonGrp("smt_importFile", edit=True, enable=(import_from == 2))

    for ctrl, prefix, func in (("smt_exportPath", "Saves to: ", _export_path),
                               ("smt_importPath", "Imports from: ", _import_path)):
        try:
            label = prefix + func()
        except RuntimeError as e:
            label = "! " + str(e)
        cmds.text(ctrl, edit=True, label=label)

    cmds.optionVar(intValue=(OPT_MODE, save_mode))
    cmds.optionVar(stringValue=(OPT_FOLDER,
                                cmds.textFieldButtonGrp("smt_folder", q=True, text=True)))
    cmds.optionVar(stringValue=(OPT_NAME,
                                cmds.textFieldGrp("smt_name", q=True, text=True)))
    cmds.optionVar(intValue=(OPT_IMPORT_FROM, import_from))
    cmds.optionVar(stringValue=(OPT_IMPORT_FILE,
                                cmds.textFieldButtonGrp("smt_importFile", q=True, text=True)))


def _browse_folder(*_):
    start = cmds.textFieldButtonGrp("smt_folder", q=True, text=True) or _temp_folder()
    r = cmds.fileDialog2(fileMode=3, dialogStyle=2, startingDirectory=start,
                         caption="Choose a folder to save to")
    if r:
        cmds.textFieldButtonGrp("smt_folder", edit=True, text=r[0])
        _refresh()


def _browse_import_file(*_):
    current = cmds.textFieldButtonGrp("smt_importFile", q=True, text=True)
    start = os.path.dirname(current) if current else _temp_folder()
    r = cmds.fileDialog2(fileFilter="Maya ASCII (*.ma)", fileMode=1, dialogStyle=2,
                         startingDirectory=start, caption="Choose a file to import")
    if r:
        cmds.radioButtonGrp("smt_importFrom", edit=True, select=2)
        cmds.textFieldButtonGrp("smt_importFile", edit=True, text=r[0].replace("\\", "/"))
        _refresh()


def _on_export(*_):
    try:
        path = _export_path()
    except RuntimeError as e:
        cmds.confirmDialog(title="Export", message=str(e), button=["OK"])
        return
    # 任意のフォルダに既存ファイルがあるときだけ上書きを確認（一時フォルダは黙って上書き）
    if cmds.radioButtonGrp("smt_saveMode", q=True, select=True) == 2 and os.path.isfile(path):
        if cmds.confirmDialog(title="Export",
                              message="File already exists. Overwrite?\n" + path,
                              button=["Overwrite", "Cancel"], defaultButton="Cancel",
                              cancelButton="Cancel", dismissString="Cancel") != "Overwrite":
            return
    try:
        meshes = export_meshes(path)
    except Exception as e:
        cmds.confirmDialog(title="Export", message=str(e), button=["OK"])
        return
    cmds.confirmDialog(title="Export",
                       message="Exported {} mesh(es).\n{}".format(len(meshes), path),
                       button=["OK"])


def _on_import(*_):
    try:
        path = _import_path()
    except RuntimeError as e:
        cmds.confirmDialog(title="Import", message=str(e), button=["OK"])
        return
    suffix = cmds.textFieldGrp("smt_suffix", q=True, text=True) or "_imp"
    replace = cmds.radioButtonGrp("smt_mode", q=True, select=True) == 1
    parent_mode = (PARENT_BY_NAME, PARENT_SELECTED, PARENT_WORLD)[
        cmds.radioButtonGrp("smt_parent", q=True, select=True) - 1]
    copy_bpm = cmds.checkBoxGrp("smt_opts", q=True, value1=True)
    try:
        done, errors = import_meshes(path, suffix, replace, parent_mode, copy_bpm)
    except Exception as e:
        cmds.confirmDialog(title="Import", message=str(e), button=["OK"])
        return

    for d in done:
        print("[{}] OK   : {}".format(TOOL_NAME, d))
    for e in errors:
        print("[{}] FAIL : {}".format(TOOL_NAME, e))
    if done:
        cmds.select(done, replace=True)

    msg = "Transferred {} mesh(es).".format(len(done))
    if errors:
        msg += "\n\nFailed:\n" + "\n".join(errors)
    cmds.confirmDialog(title="Import", message=msg, button=["OK"])
