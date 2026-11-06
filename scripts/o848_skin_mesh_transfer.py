# -*- coding: utf-8 -*-
"""
o-848 Skin Mesh Transfer
part of o-848 Tools
Copyright (c) 2026 OhashiYa
Released under the MIT License (see LICENSE)

対応予定: Maya 2023 - 2026（動作確認済み: Maya 2023, 2026 ※v1.1.0）

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

__version__ = "1.3.0"

import json
import math
import os
import traceback

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

TEMP_NS = "SMT_TMP"
TOOL_NAME = "o-848 Skin Mesh Transfer"
WINDOW = "o848SkinMeshTransferWin"

# UI 設定の保存キー（Maya を再起動しても前回の設定を引き継ぐ）
OPT_MODE = "o848_skinMeshTransfer_saveMode"  # 1=一時フォルダ 2=任意のフォルダ
OPT_FOLDER = "o848_skinMeshTransfer_folder"
OPT_NAME = "o848_skinMeshTransfer_fileName"
OPT_IMPORT_FROM = "o848_skinMeshTransfer_importFrom"  # 1=前回の書き出し先 2=別のファイル
OPT_IMPORT_FILE = "o848_skinMeshTransfer_importFile"
OPT_CLASH = (
    "o848_skinMeshTransfer_clashMode"  # 1=既存の側を改名（Replace） 2=読み込んだ側を改名（Keep）
)
OPT_IMP_SUFFIX = "o848_skinMeshTransfer_impSuffix"
OPT_OLD_SUFFIX = "o848_skinMeshTransfer_oldSuffix"
DEFAULT_NAME = "o848_skin_mesh_transfer"
INVALID_CHARS = '\\/:*?"<>|'


# ============================================================
# 名前まわり
# ============================================================
def _leaf(name):
    """DAG パスの一番下の名前（ネームスペースは残す）"""
    return name.split("|")[-1]


def _in_temp_ns(name):
    """一時ネームスペース（読み込んだもの）のノードかどうか"""
    return _leaf(name).startswith(TEMP_NS + ":")


def _strip_temp_ns(name):
    """一時ネームスペースを外した名前"""
    leaf = _leaf(name)
    return leaf[len(TEMP_NS) + 1 :] if leaf.startswith(TEMP_NS + ":") else leaf


def _basename(name):
    """パスもネームスペースも取り除いた名前"""
    return _leaf(name).split(":")[-1]


def _unique_name(base, suffix):
    """ルートネームスペースに同名が無ければそのまま、あれば接尾辞を付ける"""
    if not cmds.ls(base):
        return base
    name = base + suffix
    number = 1
    while cmds.ls(name):
        name = "{}{}{}".format(base, suffix, number)
        number += 1
    return name


def _uuid(node):
    """ノードの UUID（改名しても変わらない識別子）"""
    return cmds.ls(node, uuid=True)[0]


def _from_uuid(uid):
    """UUID から、今のフルパスを得る"""
    return cmds.ls(uid, long=True)[0]


def _deformed_shape(transform):
    """トランスフォームの下の、中間オブジェクトではないシェイプ（表示されている形）"""
    for shape in cmds.listRelatives(transform, shapes=True, fullPath=True) or []:
        if not cmds.getAttr(shape + ".intermediateObject"):
            return shape
    return None


def _skin_cluster(mesh):
    """メッシュにつながっている skinCluster（無ければ None）"""
    sc = mel.eval('findRelatedSkinCluster "{}"'.format(mesh))
    return sc or None


def _resolve_in_scene(name):
    """一時NS内の名前から、先シーンの同名トランスフォーム（骨・親）を探す。
    見つからなければ None、複数あればエラー。"""
    candidates = (
        (_strip_temp_ns(name), False),  # 元のネームスペース付きのまま
        (_basename(name), False),  # ネームスペースなし
        (_basename(name), True),  # どのネームスペースでも
    )
    for pattern, recursive in candidates:
        found = [
            n
            for n in (cmds.ls(pattern, long=True, type="transform", recursive=recursive) or [])
            if not _in_temp_ns(n)
        ]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise RuntimeError("Multiple nodes named: " + pattern)
    return None


# ============================================================
# マテリアルのネットワーク
# ============================================================
def _shading_network(shading_group):
    """シェーディンググループと、その先のマテリアル・テクスチャのノードの一覧"""
    network_nodes = [shading_group]
    for attr in ("surfaceShader", "displacementShader", "volumeShader"):
        for upstream in (
            cmds.listConnections(
                "{}.{}".format(shading_group, attr), source=True, destination=False
            )
            or []
        ):
            network_nodes += cmds.listHistory(upstream) or []
    network_nodes += cmds.listConnections(shading_group, type="materialInfo") or []
    return list(dict.fromkeys(network_nodes))


# ============================================================
# 1. 書き出し（元シーン）
# ============================================================
INFO_NODE = "o848_smt_info"  # 書き出しファイルに入れる情報ノード（選択したグループを記録）


def _is_group(node):
    """シェイプを持たない普通のトランスフォーム（骨は除く）"""
    return cmds.nodeType(node) == "transform" and not cmds.listRelatives(node, shapes=True)


def _collect_skinned_meshes(roots):
    """選択ノードとその子孫から、スキン付きメッシュのトランスフォームを集める。
    グループを選んでも中のメッシュを拾い、スキンの無いメッシュや骨・ロケーターは無視する。"""
    result = []
    for root in cmds.ls(roots, type="transform", long=True) or []:
        candidates = [root] + (
            cmds.listRelatives(root, allDescendents=True, type="transform", fullPath=True) or []
        )
        for node in candidates:
            if node in result:
                continue
            shape = _deformed_shape(node)
            if shape and cmds.nodeType(shape) == "mesh" and _skin_cluster(node):
                result.append(node)
    return result


def export_meshes(path, meshes=None):
    """選択したメッシュ（またはグループ）を、骨・スキン・マテリアルごと .ma に書き出す。
    戻り値：書き出したメッシュの一覧"""
    roots = meshes or cmds.ls(selection=True, long=True)
    if not roots:
        raise RuntimeError("Select skinned meshes or a group.")
    meshes = _collect_skinned_meshes(roots)
    if not meshes:
        raise RuntimeError("No skinned meshes in selection.")

    # 選択されたグループ（中にスキン付きメッシュがあるもの）を記録する
    group_roots = []
    for root in cmds.ls(roots, type="transform", long=True) or []:
        if _is_group(root) and any(mesh.startswith(root + "|") for mesh in meshes):
            group_roots.append(root)

    nodes = set()
    for mesh in meshes:
        skin_cluster = _skin_cluster(mesh)
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
        nodes.add(mesh)
        nodes.update(shapes)
        # 変形ヒストリ（skinCluster, groupParts, tweak など）。骨から先はたどらない
        for shape in shapes:
            nodes.update(cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], long=True))
        nodes.update(cmds.ls(cmds.skinCluster(skin_cluster, q=True, influence=True), long=True))
        nodes.update(cmds.listConnections(skin_cluster, type="dagPose") or [])
        for shape in shapes:
            for shading_group in set(cmds.listConnections(shape, type="shadingEngine") or []):
                nodes.update(_shading_network(shading_group))

    # DAG の祖先も含める（骨とグループの階層を保つため）
    for node in list(nodes):
        if cmds.objExists(node) and cmds.objectType(node, isAType="dagNode"):
            parts = cmds.ls(node, long=True)[0].split("|")[1:]
            for depth in range(1, len(parts)):
                nodes.add("|" + "|".join(parts[:depth]))

    defaults = set(cmds.ls(defaultNodes=True))
    nodes = [node for node in nodes if cmds.objExists(node) and node not in defaults]

    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    previous_selection = cmds.ls(selection=True, long=True)
    info_node = None
    try:
        if group_roots:
            info_node = cmds.createNode("network", name=INFO_NODE, skipSelect=True)
            cmds.addAttr(info_node, longName="groupRoots", dataType="string")
            cmds.setAttr(info_node + ".groupRoots", json.dumps(group_roots), type="string")
            nodes.append(info_node)
        cmds.select(nodes, replace=True, noExpand=True)
        cmds.file(
            path,
            force=True,
            type="mayaAscii",
            options="v=0;",
            preserveReferences=False,
            exportSelectedStrict=True,
        )
    finally:
        if info_node and cmds.objExists(info_node):
            cmds.delete(info_node)
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)
    return meshes


# ============================================================
# 2. 読み込み・移植（先シーン）
# ============================================================
PARENT_BY_NAME = "name"  # 元の親と同じ名前のノードの下（無ければワールド）
PARENT_SELECTED = "selected"  # 読み込む前に選択していたノードの下
PARENT_WORLD = "world"  # ワールド直下

CLASH_RENAME_IMPORTED = "imported"  # 名前が競合したら、読み込んだ側に接尾辞（_imp）
CLASH_RENAME_EXISTING = "existing"  # 名前が競合したら、既存の側に接尾辞（_old）

MAT_USE_EXISTING = "existing"  # 同名マテリアルがあれば既存に統一
MAT_USE_IMPORTED = "imported"  # 同名マテリアルがあれば読み込んだものに統一（既存は削除）
MAT_KEEP_BOTH = "both"  # 両方残す（読み込んだ側に _imp）


def _free_name(stem):
    """stem が空いていればそのまま、無ければ stem1, stem2 …"""
    if not cmds.ls(stem):
        return stem
    number = 1
    while cmds.ls("{}{}".format(stem, number)):
        number += 1
    return "{}{}".format(stem, number)


class _NameResolver(object):
    """読み込んだノードの名前を決める。
    existing モードでは、同名の既存ノードを _old 付きに改名して、読み込んだ側に元の名前を渡す。"""

    def __init__(self, mode, new_suffix, old_suffix):
        """mode：CLASH_RENAME_EXISTING（既存を改名）か CLASH_RENAME_IMPORTED（読み込んだ側を改名）
        new_suffix：読み込んだ側に付ける接尾辞　old_suffix：既存の側に付ける接尾辞"""
        self.mode = mode
        self.new_suffix = new_suffix or "_imp"
        self.old_suffix = old_suffix or "_old"
        self.protected = set()  # 改名してはいけないノードの UUID（今回作ったもの・使う親など）
        self.renamed = []  # (旧名, 新名)
        self.not_renamed = []  # 改名できず、読み込んだ側に _imp を付けたもの
        self.old_meshes = set()  # _old にした既存メッシュの UUID
        self.old_by_name = {}  # 元の名前 -> _old にした既存 DAG ノードの UUID
        self.replaced_materials = []  # 読み込んだものに統一したマテリアル名
        self._rename_log = []  # メッシュ1つ分の改名の記録（失敗したら元に戻す）

    def begin_mesh(self):
        """メッシュ 1 つ分の処理を始める（改名の記録をリセットする）"""
        self._rename_log = []

    def undo_renames(self):
        """このメッシュの処理中に行った既存ノードの改名を元に戻す"""
        for node_uid, previous_name in reversed(self._rename_log):
            if cmds.ls(node_uid):
                try:
                    cmds.rename(_from_uuid(node_uid), ":" + previous_name, ignoreShape=True)
                except RuntimeError:
                    pass
        undone = {previous_name for _, previous_name in self._rename_log}
        self.renamed = [entry for entry in self.renamed if entry[0] not in undone]
        for previous_name in undone:
            self.old_by_name.pop(previous_name, None)
        self._rename_log = []

    def _rename(self, node, new_name):
        """改名し、あとで元に戻せるように記録する"""
        node_uid, previous_name = _uuid(node), _basename(node)
        # ignoreShape：Maya が自動でシェイプ名を変えないようにする（シェイプは自分で改名する）
        renamed_to = cmds.rename(node, ":" + new_name, ignoreShape=True)
        self._rename_log.append((node_uid, previous_name))
        return renamed_to

    def protect(self, node):
        """このノードは改名しない（今回作ったもの、使う親など）"""
        if node and cmds.objExists(node):
            self.protected.add(_uuid(node))

    def _can_rename(self, node):
        """既存ノードを改名してよいか（骨、骨を含むグループ、リファレンス、ロックは不可）"""
        if _uuid(node) in self.protected:
            return False
        if cmds.nodeType(node) == "joint":
            return False
        if cmds.objectType(node, isAType="dagNode") and cmds.listRelatives(
            node, allDescendents=True, type="joint"
        ):
            return False  # 骨を含むグループ（リグ）は触らない
        try:
            if cmds.referenceQuery(node, isNodeReferenced=True):
                return False
        except RuntimeError:
            pass
        if (cmds.lockNode(node, q=True, lock=True) or [False])[0]:
            return False
        return True

    def _rename_existing(self, node):
        """既存ノードに old_suffix を付けて改名する（メッシュならシェイプも）"""
        old_name = _basename(node)
        is_mesh = (
            cmds.nodeType(node) == "transform"
            and _deformed_shape(node)
            and cmds.nodeType(_deformed_shape(node)) == "mesh"
        )
        node_uid = _uuid(node)
        renamed_to = self._rename(node, _free_name(old_name + self.old_suffix))
        self.renamed.append((old_name, renamed_to))
        if cmds.objectType(_from_uuid(node_uid), isAType="dagNode"):
            self.old_by_name[old_name] = node_uid
        if is_mesh:
            self.old_meshes.add(node_uid)
            # シェイプも「新しい名前 + Shape」にそろえる（後で読み込む側のシェイプ名と競合させない）
            for shape in cmds.listRelatives(_from_uuid(node_uid), shapes=True, fullPath=True) or []:
                self._rename(shape, _free_name(_basename(renamed_to) + "Shape"))
        return renamed_to

    def name_for_new_node(self, base):
        """メッシュ・グループなど DAG ノード用"""
        clashes = cmds.ls(base, long=True) or []
        if not clashes:
            return base
        if self.mode == CLASH_RENAME_EXISTING and all(self._can_rename(node) for node in clashes):
            log_start = len(self._rename_log)
            try:
                for node_uid in [_uuid(node) for node in clashes]:
                    self._rename_existing(_from_uuid(node_uid))
                return base
            except RuntimeError:
                # 改名できなかった → この名前の分だけ元に戻し、読み込んだ側に _imp を付ける
                traceback.print_exc()
                undo_ops, self._rename_log = (
                    self._rename_log[log_start:],
                    self._rename_log[:log_start],
                )
                for node_uid, previous_name in reversed(undo_ops):
                    if cmds.ls(node_uid):
                        try:
                            cmds.rename(_from_uuid(node_uid), ":" + previous_name, ignoreShape=True)
                        except RuntimeError:
                            pass
                self.renamed = [entry for entry in self.renamed if entry[0] != base]
                self.old_by_name.pop(base, None)
        if self.mode == CLASH_RENAME_EXISTING:
            self.not_renamed.append(base)
        return _unique_name(base, self.new_suffix)

    def name_for_new_skin_cluster(self, base):
        """skinCluster 用：_old にしたメッシュのものだけ、既存側を改名する"""
        clashes = cmds.ls(base, long=True) or []
        if not clashes:
            return base
        if self.mode == CLASH_RENAME_EXISTING:

            def belongs_to_old_mesh(skin_cluster):
                """この skinCluster が、今回 _old にしたメッシュのものか"""
                if cmds.nodeType(skin_cluster) != "skinCluster":
                    return False
                geometries = cmds.skinCluster(skin_cluster, q=True, geometry=True) or []
                parents = [
                    cmds.listRelatives(geometry, parent=True, fullPath=True)
                    for geometry in geometries
                ]
                return bool(parents) and all(
                    parent and _uuid(parent[0]) in self.old_meshes for parent in parents
                )

            if all(belongs_to_old_mesh(node) and self._can_rename(node) for node in clashes):
                try:
                    for node_uid in [_uuid(node) for node in clashes]:
                        node = _from_uuid(node_uid)
                        renamed_to = self._rename(
                            node, _free_name(_basename(node) + self.old_suffix)
                        )
                        self.renamed.append((base, renamed_to))
                    return base
                except RuntimeError:
                    traceback.print_exc()
        return _unique_name(base, self.new_suffix)


class _GroupRestorer(object):
    """書き出し時に選択されていたグループを、移植先に作り直す（同じ場所に同名があれば再利用）"""

    def __init__(self, name_resolver, parent_mode, parent_uuid):
        """書き出しファイルの情報ノードから、選択されていたグループを読む"""
        self.name_resolver = name_resolver
        self.parent_mode = parent_mode
        self.parent_uuid = parent_uuid
        self.group_roots = []
        self.restored = {}  # 一時NS内のグループのパス -> 移植先のグループの UUID
        self.created_uuids = set()  # 新しく作ったグループの UUID
        info_nodes = cmds.ls(TEMP_NS + ":" + INFO_NODE + "*", type="network") or []
        for info_node in info_nodes:
            if cmds.attributeQuery("groupRoots", node=info_node, exists=True):
                for group_path in json.loads(cmds.getAttr(info_node + ".groupRoots") or "[]"):
                    temp_path = "|" + "|".join(
                        TEMP_NS + ":" + part for part in group_path.split("|")[1:]
                    )
                    if cmds.objExists(temp_path):
                        self.group_roots.append(cmds.ls(temp_path, long=True)[0])

    def top_group_of(self, mesh):
        """mesh を含む、いちばん上の選択グループ（無ければ None）"""
        candidates = [
            group_root for group_root in self.group_roots if mesh.startswith(group_root + "|")
        ]
        return min(candidates, key=len) if candidates else None

    def parent_for_mesh(self, mesh):
        """mesh の置き場所（移植先のノードのフルパス、またはワールドなら None）"""
        group_root = self.top_group_of(mesh)
        if not group_root:
            return _target_parent(mesh, self.parent_mode, self.parent_uuid)
        mesh_parent = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
        # group_root からメッシュの親までのグループを、上から順に作る（または再利用）
        path_parts = mesh_parent[len(group_root) :].split("|")[1:]
        group_paths = [group_root] + [
            group_root + "|" + "|".join(path_parts[: depth + 1]) for depth in range(len(path_parts))
        ]
        parent = _target_parent(group_root, self.parent_mode, self.parent_uuid)
        for temp_group in group_paths:
            parent = self._get_or_create_group(temp_group, parent)
        return parent

    def _get_or_create_group(self, temp_group, parent):
        """temp_group と同じ名前のグループを parent の下で探し、あれば使う。無ければ作る。
        戻り値：移植先のグループのフルパス"""
        if temp_group in self.restored and cmds.ls(self.restored[temp_group]):
            return _from_uuid(self.restored[temp_group])
        group_name = _basename(temp_group)
        # 同じ場所に同名のグループがあれば再利用
        if parent:
            siblings = (
                cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []
            )
        else:
            siblings = cmds.ls(assemblies=True, long=True) or []
        for sibling in siblings:
            if not _in_temp_ns(sibling) and _basename(sibling) == group_name and _is_group(sibling):
                self.name_resolver.protect(sibling)
                self.restored[temp_group] = _uuid(sibling)
                return sibling
        # 無ければ作る（元のローカルの位置・回転・スケールをコピー）
        new_name = self.name_resolver.name_for_new_node(group_name)
        kwargs = {"parent": parent} if parent else {}
        group = cmds.createNode("transform", name=new_name, skipSelect=True, **kwargs)
        group = cmds.ls(group, long=True)[0]
        cmds.xform(
            group,
            objectSpace=True,
            matrix=cmds.xform(temp_group, q=True, objectSpace=True, matrix=True),
        )
        self.name_resolver.protect(group)
        self.restored[temp_group] = _uuid(group)
        self.created_uuids.add(_uuid(group))
        return group


def _target_parent(node, parent_mode, parent_uuid):
    """置き場所の設定に従って、node の親を決める"""
    if parent_mode == PARENT_BY_NAME:
        source_parent = cmds.listRelatives(node, parent=True, fullPath=True)
        if source_parent:
            try:
                return _resolve_in_scene(source_parent[0])
            except RuntimeError:
                return None
        return None
    if parent_mode == PARENT_SELECTED:
        if not cmds.ls(parent_uuid):
            raise RuntimeError("Parent node not found.")
        return _from_uuid(parent_uuid)
    return None


POSE_POSITION_TOLERANCE = 0.001  # 位置のずれの許容値（シーンの単位、通常 cm）
POSE_ROTATION_TOLERANCE = 0.01  # 回転のずれの許容値（度）


class ImportCancelled(Exception):
    """利用者が確認ダイアログで読み込みを取りやめた"""


def bind_pose_differences(src_meshes):
    """元のメッシュのバインド時の骨の位置と、移植先の同名の骨の今の位置を比べる。
    移植先の骨に記録されたバインドポーズは使わない（当てにならない場合があるため）。
    戻り値：[(骨の名前, 位置のずれ, 回転のずれ〔度〕, (tx, ty, tz), (rx, ry, rz))]（ずれの大きい順）
    tx〜rz はワールド空間でのずれ（回転は度、XYZ 順のオイラー角）"""
    differences = {}
    for src_mesh in src_meshes:
        skin_cluster = _skin_cluster(src_mesh)
        skin_fn = _skin_fn(skin_cluster)
        for influence in skin_fn.influenceObjects():
            try:
                target_joint = _resolve_in_scene(influence.fullPathName())
            except RuntimeError:
                continue  # 同名の骨が複数ある → 移植の処理でエラーとして扱う
            if not target_joint:
                continue  # 足りない骨 → 移植の処理でエラーとして扱う
            index = skin_fn.indexForInfluenceObject(influence)
            bind_pre_matrix = om.MMatrix(
                cmds.getAttr("{}.bindPreMatrix[{}]".format(skin_cluster, index))
            )
            src_bind = om.MTransformationMatrix(bind_pre_matrix.inverse())
            selection = om.MSelectionList()
            selection.add(target_joint)
            target_now = om.MTransformationMatrix(selection.getDagPath(0).inclusiveMatrix())

            offset = target_now.translation(om.MSpace.kWorld) - src_bind.translation(
                om.MSpace.kWorld
            )
            distance = offset.length()
            # 元のバインド時の向きから、今の向きまでの回転（ワールド空間）
            relative = src_bind.rotation(asQuaternion=True).inverse() * target_now.rotation(
                asQuaternion=True
            )
            angle = math.degrees(2.0 * math.acos(min(1.0, abs(relative.w))))
            if distance > POSE_POSITION_TOLERANCE or angle > POSE_ROTATION_TOLERANCE:
                name = _basename(target_joint)
                if name not in differences or differences[name][0] < distance:
                    euler = relative.asEulerRotation()
                    differences[name] = (
                        distance,
                        angle,
                        (offset.x, offset.y, offset.z),
                        tuple(math.degrees(v) for v in (euler.x, euler.y, euler.z)),
                    )
    return sorted(
        ((name,) + values for name, values in differences.items()), key=lambda x: (-x[1], -x[2])
    )


def import_meshes(
    path,
    mat_mode=MAT_USE_EXISTING,
    parent_mode=PARENT_BY_NAME,
    copy_bind_matrix=False,
    clash_mode=CLASH_RENAME_IMPORTED,
    new_suffix="_imp",
    old_suffix="_old",
    confirm_pose_difference=None,
):
    """ファイルを読み込み、中のスキン付きメッシュを移植先の骨に付け直す。
    confirm_pose_difference：copy_bind_matrix がオフで、移植先の骨が元のバインド時の位置と
    違うときに呼ぶ関数。ずれの一覧を受け取り、続けるなら True を返す。
    取りやめた場合は ImportCancelled を送出する（読み込んだものは片付ける）。
    戻り値：(移植したメッシュの一覧, 失敗の一覧, _NameResolver)"""
    if not os.path.isfile(path):
        raise RuntimeError("File not found: " + path)

    # 「選択中のノード」は読み込みで選択が変わる前に確定させる
    parent_uuid = None
    if parent_mode == PARENT_SELECTED:
        selection = cmds.ls(selection=True, type="transform", long=True) or []
        if len(selection) != 1:
            raise RuntimeError("Select one parent node.")
        parent_uuid = _uuid(selection[0])

    cmds.namespace(setNamespace=":")
    if cmds.namespace(exists=TEMP_NS):
        cmds.namespace(removeNamespace=TEMP_NS, deleteNamespaceContent=True)

    cmds.file(
        path,
        i=True,
        namespace=TEMP_NS,
        preserveReferences=False,
        ignoreVersion=True,
        options="v=0;",
    )

    name_resolver = _NameResolver(clash_mode, new_suffix, old_suffix)
    if parent_uuid:
        name_resolver.protected.add(parent_uuid)

    src_meshes = []
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        if _in_temp_ns(transform) and transform not in src_meshes and _skin_cluster(transform):
            src_meshes.append(transform)

    done, errors = [], []
    shading_group_map = {}  # 読み込んだSG -> 差し替え先の既存SG
    try:
        # Keep the original bind pose がオフのときは、移植先の骨の今の姿勢で付けるので、
        # 移植先がポーズを取っていないか（骨の位置が元と違わないか）を先に確認する
        if not copy_bind_matrix and confirm_pose_difference:
            differences = bind_pose_differences(src_meshes)
            if differences and not confirm_pose_difference(differences):
                raise ImportCancelled()

        group_restorer = _GroupRestorer(name_resolver, parent_mode, parent_uuid)
        placed = []  # (一時NS内のメッシュのパス, 移植したメッシュの UUID)
        for src_mesh in src_meshes:
            name_resolver.begin_mesh()
            try:
                new_mesh = _transfer_mesh(src_mesh, name_resolver, group_restorer, copy_bind_matrix)
            except Exception as error:
                # 失敗したメッシュの分の改名は元に戻す（古いメッシュだけ _old になるのを防ぐ）
                print("[{}] ERROR: {}".format(TOOL_NAME, _strip_temp_ns(src_mesh)))
                traceback.print_exc()
                name_resolver.undo_renames()
                errors.append("{} : {}".format(_strip_temp_ns(src_mesh), error))
                continue
            try:
                _apply_materials(new_mesh, name_resolver, mat_mode, shading_group_map)
            except Exception as error:
                print("[{}] ERROR (material): {}".format(TOOL_NAME, _strip_temp_ns(src_mesh)))
                traceback.print_exc()
                errors.append("{} (material) : {}".format(_strip_temp_ns(src_mesh), error))
            done.append(new_mesh)
            placed.append((src_mesh, _uuid(new_mesh)))
        try:
            _restore_order(placed, group_restorer, name_resolver)
        except Exception as error:
            cmds.warning("[{}] Could not restore node order: {}".format(TOOL_NAME, error))
        done = [_from_uuid(mesh_uid) for _, mesh_uid in placed if cmds.ls(mesh_uid)]
    finally:
        # 読み込んだ骨・元メッシュ・使わなかったマテリアル等をまとめて削除
        cmds.namespace(setNamespace=":")
        if cmds.namespace(exists=TEMP_NS):
            cmds.namespace(removeNamespace=TEMP_NS, deleteNamespaceContent=True)
    return done, errors, name_resolver


def _child_transforms(parent):
    """parent の子トランスフォーム（並び順どおり）。parent が None ならワールド直下"""
    if parent:
        return cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []
    return [
        node
        for node in (cmds.ls(assemblies=True, long=True) or [])
        if cmds.nodeType(node) != "dagContainer"
    ]


def _parent_uuid(node):
    """親の UUID（ワールド直下なら None）"""
    parent = cmds.listRelatives(node, parent=True, fullPath=True)
    return _uuid(parent[0]) if parent else None


def _restore_order(placed, group_restorer, name_resolver):
    """書き出し元の並び順を再現する（FBX のノード順を崩さないため）。
    対象：Replace existing のときの全ノード、および新しく作ったグループの中身"""
    mapping = dict(group_restorer.restored)
    mapping.update(dict(placed))
    items = [
        (temp_path, sibling_uid)
        for temp_path, sibling_uid in group_restorer.restored.items()
        if sibling_uid in group_restorer.created_uuids
    ] + list(placed)
    dfs_order = {path: index for index, path in enumerate(cmds.ls(dag=True, long=True) or [])}
    items.sort(key=lambda item: dfs_order.get(item[0], 0))

    for temp_path, node_uid in items:
        if not cmds.ls(node_uid):
            continue
        node = _from_uuid(node_uid)
        parent_uid = _parent_uuid(node)
        if not (
            name_resolver.mode == CLASH_RENAME_EXISTING
            or parent_uid in group_restorer.created_uuids
        ):
            continue
        temp_parent = cmds.listRelatives(temp_path, parent=True, fullPath=True)
        source_siblings = (
            _child_transforms(temp_parent[0])
            if temp_parent
            else [path for path in _child_transforms(None) if _in_temp_ns(path)]
        )
        if temp_path not in source_siblings:
            continue

        # 書き出し元で直前にあったノードのうち、同じ親の下に移ってきているもの
        anchor = None
        for previous_sibling in reversed(source_siblings[: source_siblings.index(temp_path)]):
            sibling_uid = mapping.get(previous_sibling)
            if (
                sibling_uid
                and cmds.ls(sibling_uid)
                and _parent_uuid(_from_uuid(sibling_uid)) == parent_uid
            ):
                anchor = _from_uuid(sibling_uid)
                break

        parent = cmds.listRelatives(node, parent=True, fullPath=True)
        siblings = _child_transforms(parent[0] if parent else None)
        current_index = siblings.index(node)
        other_siblings = [path for path in siblings if path != node]
        if anchor:
            target_index = other_siblings.index(anchor) + 1
        else:
            old_uid = name_resolver.old_by_name.get(_basename(node))
            old_node = _from_uuid(old_uid) if old_uid and cmds.ls(old_uid) else None
            if old_node and _parent_uuid(old_node) == parent_uid:
                target_index = other_siblings.index(old_node)  # 置き換えた古いノードの位置へ
            elif parent_uid in group_restorer.created_uuids:
                target_index = 0  # 新しいグループでは先頭から
            else:
                continue
        if target_index != current_index:
            cmds.reorder(node, relative=target_index - current_index)


def _transfer_mesh(src_mesh, name_resolver, group_restorer, copy_bind_matrix):
    """一時ネームスペースのメッシュ 1 つを、移植先の骨に付け直した新しいメッシュとして作る。
    戻り値：新しいメッシュのフルパス"""
    src_skin_cluster = _skin_cluster(src_mesh)
    src_shape = _deformed_shape(src_mesh)
    src_skin_fn = _skin_fn(src_skin_cluster)
    src_influences = [p.fullPathName() for p in src_skin_fn.influenceObjects()]

    # 1. 先シーンの骨を名前で探す（足りなければ何も作らずに止める）
    influence_map, missing = {}, []
    for influence in src_influences:
        dst_influence = _resolve_in_scene(influence)
        if dst_influence:
            influence_map[influence] = dst_influence
        else:
            missing.append(_strip_temp_ns(influence))
    if missing:
        raise RuntimeError("Missing joints: " + ", ".join(missing))

    # 2. 置き場所（グループを作り直す場合はここで作る）
    parent = group_restorer.parent_for_mesh(src_mesh)
    parent_uid = _uuid(parent) if parent else None

    # 3. スキンを切った状態（バインド時の形）で複製
    envelope = cmds.getAttr(src_skin_cluster + ".envelope")
    cmds.setAttr(src_skin_cluster + ".envelope", 0)
    try:
        new_mesh = cmds.duplicate(src_mesh, returnRootsOnly=True)[0]
    finally:
        cmds.setAttr(src_skin_cluster + ".envelope", envelope)
    mesh_uid = _uuid(new_mesh)
    name_resolver.protected.add(mesh_uid)
    # 複製はシーンの通常の場所に元と同じ名前で作られるため、そのままだと
    # 正式な名前を付けるときに自分自身と競合する。一時ネームスペースの仮の名前に退避させる。
    cmds.rename(_from_uuid(mesh_uid), TEMP_NS + ":o848_smt_dup#", ignoreShape=True)
    for shape in cmds.listRelatives(_from_uuid(mesh_uid), shapes=True, fullPath=True) or []:
        name_resolver.protected.add(_uuid(shape))
    for shape_uid in [
        _uuid(shape)
        for shape in cmds.listRelatives(_from_uuid(mesh_uid), shapes=True, fullPath=True) or []
    ]:
        cmds.rename(_from_uuid(shape_uid), TEMP_NS + ":o848_smt_dupShape#")

    try:
        # 中間シェイプや子ノードを除去
        for child in cmds.listRelatives(_from_uuid(mesh_uid), children=True, fullPath=True) or []:
            if cmds.objectType(child, isAType="shape") and not cmds.getAttr(
                child + ".intermediateObject"
            ):
                continue
            cmds.delete(child)

        if parent_uid:
            cmds.parent(_from_uuid(mesh_uid), _from_uuid(parent_uid))
        elif cmds.listRelatives(_from_uuid(mesh_uid), parent=True):
            cmds.parent(_from_uuid(mesh_uid), world=True)

        # 4. ルートネームスペースへ改名（競合の扱いは設定に従う）
        #    名前を先に決めてから改名する。決める途中で既存ノードが改名されるため、
        #    パスは改名の直前に UUID から取り直す。
        new_name = name_resolver.name_for_new_node(_basename(src_mesh))
        cmds.rename(_from_uuid(mesh_uid), ":" + new_name, ignoreShape=True)
        shape_uid = _uuid(_deformed_shape(_from_uuid(mesh_uid)))
        shape_name = name_resolver.name_for_new_node(_basename(_from_uuid(mesh_uid)) + "Shape")
        cmds.rename(_from_uuid(shape_uid), ":" + shape_name)
        new_mesh = _from_uuid(mesh_uid)

        # 5. 先シーンの骨にバインド
        dst_influences = [influence_map[influence] for influence in src_influences]
        joints = [influence for influence in dst_influences if cmds.nodeType(influence) == "joint"]
        other_influences = [influence for influence in dst_influences if influence not in joints]
        if not joints:
            raise RuntimeError("No joint influences.")
        cmds.select(clear=True)
        new_skin_cluster = cmds.skinCluster(
            joints + [new_mesh],
            toSelectedBones=True,
            bindMethod=0,
            removeUnusedInfluence=False,
            obeyMaxInfluences=False,
            name=name_resolver.name_for_new_skin_cluster(_basename(src_skin_cluster)),
        )[0]
        for influence in other_influences:
            cmds.skinCluster(new_skin_cluster, edit=True, addInfluence=influence, weight=0.0)
        new_skin_fn = _skin_fn(new_skin_cluster)

        # 6. バインド時の行列を元からコピー（先の骨がポーズ中でも正しく付く）
        if copy_bind_matrix:
            _copy_bind_matrices(
                src_skin_cluster, src_skin_fn, new_skin_cluster, new_skin_fn, influence_map
            )

        # 7. ウェイト転写（頂点番号が同一なので番号で完全コピー）
        _copy_weights(src_skin_fn, src_shape, new_skin_fn, _deformed_shape(new_mesh), influence_map)

        # 8. skinCluster の設定をそろえる
        for attr in (
            "skinningMethod",
            "normalizeWeights",
            "maintainMaxInfluences",
            "maxInfluences",
            "deformUserNormals",
        ):
            try:
                cmds.setAttr(
                    new_skin_cluster + "." + attr, cmds.getAttr(src_skin_cluster + "." + attr)
                )
            except Exception:
                pass
    except Exception:
        if cmds.ls(mesh_uid):
            cmds.delete(_from_uuid(mesh_uid))
        raise

    return _from_uuid(mesh_uid)


# ============================================================
# スキン（API）
# ============================================================
def _skin_fn(skin_cluster):
    """skinCluster を API（MFnSkinCluster）で扱うためのオブジェクト"""
    selection = om.MSelectionList()
    selection.add(skin_cluster)
    return oma.MFnSkinCluster(selection.getDependNode(0))


def _dag_and_all_verts(shape):
    """シェイプの DAG パスと、全頂点を表すコンポーネント"""
    selection = om.MSelectionList()
    selection.add(shape)
    dag = selection.getDagPath(0)
    component_fn = om.MFnSingleIndexedComponent()
    components = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.setCompleteData(om.MFnMesh(dag).numVertices)
    return dag, components


def _copy_weights(src_fn, src_shape, dst_fn, dst_shape, influence_map):
    """頂点番号どおりにウェイトをコピーする（インフルエンスは influence_map で対応付け）"""
    src_dag, src_components = _dag_and_all_verts(src_shape)
    dst_dag, dst_components = _dag_and_all_verts(dst_shape)
    if om.MFnMesh(src_dag).numVertices != om.MFnMesh(dst_dag).numVertices:
        raise RuntimeError("Vertex count mismatch.")

    weights, _ = src_fn.getWeights(src_dag, src_components)
    dst_influence_index = {
        influence.fullPathName(): index for index, influence in enumerate(dst_fn.influenceObjects())
    }
    influence_order = om.MIntArray(
        [
            dst_influence_index[influence_map[influence.fullPathName()]]
            for influence in src_fn.influenceObjects()
        ]
    )
    dst_fn.setWeights(dst_dag, dst_components, influence_order, weights, False)

    if cmds.getAttr(src_fn.name() + ".skinningMethod") == 2:  # デュアルクォータニオンのブレンド
        dst_fn.setBlendWeights(
            dst_dag, dst_components, src_fn.getBlendWeights(src_dag, src_components)
        )


def _copy_bind_matrices(src_skin_cluster, src_fn, dst_skin_cluster, dst_fn, influence_map):
    """元の skinCluster のバインド時の行列を、新しい skinCluster にコピーする"""
    for influence in src_fn.influenceObjects():
        src_index = src_fn.indexForInfluenceObject(influence)
        selection = om.MSelectionList()
        selection.add(influence_map[influence.fullPathName()])
        dst_index = dst_fn.indexForInfluenceObject(selection.getDagPath(0))
        matrix = cmds.getAttr("{}.bindPreMatrix[{}]".format(src_skin_cluster, src_index))
        cmds.setAttr(
            "{}.bindPreMatrix[{}]".format(dst_skin_cluster, dst_index), matrix, type="matrix"
        )
    cmds.setAttr(
        dst_skin_cluster + ".geomMatrix",
        cmds.getAttr(src_skin_cluster + ".geomMatrix"),
        type="matrix",
    )


# ============================================================
# マテリアル整理
# ============================================================
def _apply_materials(new_mesh, name_resolver, mat_mode, shading_group_map):
    """新しいメッシュのマテリアルを、設定（既存に統一／読み込みに統一／両方残す）に従って整理する"""
    shape = _deformed_shape(new_mesh)
    for shading_group in list(
        dict.fromkeys(cmds.listConnections(shape, type="shadingEngine") or [])
    ):
        if not _in_temp_ns(shading_group):
            continue  # 移し終えたSG、または initialShadingGroup など既存のもの
        if shading_group in shading_group_map:
            _reassign_faces(new_mesh, shape, shading_group, shading_group_map[shading_group])
            continue
        materials = (
            cmds.listConnections(shading_group + ".surfaceShader", source=True, destination=False)
            or []
        )
        existing = []
        if materials:
            existing = [
                material
                for material in (cmds.ls(_basename(materials[0]), materials=True) or [])
                if not _in_temp_ns(material)
            ]
        if existing and mat_mode == MAT_USE_IMPORTED:
            if _replace_material(existing[0], shading_group, materials[0], name_resolver):
                continue
            name_resolver.not_renamed.append(
                _basename(materials[0])
            )  # 置き換えられない → 既存を使う
        if existing and mat_mode in (MAT_USE_EXISTING, MAT_USE_IMPORTED):
            shading_group_map[shading_group] = _shading_group_of(existing[0])
            _reassign_faces(new_mesh, shape, shading_group, shading_group_map[shading_group])
            continue
        # 同名マテリアルが無い／Keep both → ネットワークごと移して残す
        _rename_network(shading_group, name_resolver.new_suffix)


def _replace_material(old_mat, temp_shading_group, temp_mat, name_resolver):
    """既存のマテリアルを、読み込んだマテリアルで置き換える。
    既存のマテリアルを使っていたものはすべて新しいマテリアルに付け替え、既存は削除する。"""
    if old_mat in (cmds.ls(defaultNodes=True) or []) or not name_resolver._can_rename(old_mat):
        return False
    old_shading_groups = [
        shading_group
        for shading_group in (
            cmds.listConnections(old_mat, source=False, destination=True, type="shadingEngine")
            or []
        )
        if old_mat
        in (
            cmds.listConnections(shading_group + ".surfaceShader", source=True, destination=False)
            or []
        )
    ]
    old_shading_groups = list(dict.fromkeys(old_shading_groups))
    defaults = set(cmds.ls(defaultNodes=True) or [])
    if any(
        shading_group in defaults or not name_resolver._can_rename(shading_group)
        for shading_group in old_shading_groups
    ):
        return False

    members = []
    for shading_group in old_shading_groups:
        members += cmds.sets(shading_group, q=True) or []
    mat_name, shading_group_name = _basename(temp_mat), _basename(temp_shading_group)
    mat_uid, shading_group_uid = _uuid(temp_mat), _uuid(temp_shading_group)

    _rename_network(temp_shading_group, name_resolver.new_suffix)  # いったん接尾辞付きで移す
    new_shading_group = _from_uuid(shading_group_uid)
    if members:
        cmds.sets(members, edit=True, forceElement=new_shading_group)
    cmds.delete(old_shading_groups + [old_mat])

    # 空いた名前を引き継ぐ
    if not cmds.ls(mat_name):
        cmds.rename(_from_uuid(mat_uid), ":" + mat_name)
    if not cmds.ls(shading_group_name):
        cmds.rename(_from_uuid(shading_group_uid), ":" + shading_group_name)
    name_resolver.replaced_materials.append(mat_name)
    return True


def _shading_group_of(material):
    """マテリアルがつながっているシェーディンググループ（無ければ作る）"""
    for shading_group in (
        cmds.listConnections(material, source=False, destination=True, type="shadingEngine") or []
    ):
        if material in (
            cmds.listConnections(shading_group + ".surfaceShader", source=True, destination=False)
            or []
        ):
            return shading_group
    shading_group = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True, name=material + "SG"
    )
    cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
    return shading_group


def _reassign_faces(new_mesh, shape, src_shading_group, dst_shading_group):
    """src_shading_group に入っている new_mesh の面を、dst_shading_group に割り当て直す"""
    targets = {cmds.ls(new_mesh, long=True)[0], cmds.ls(shape, long=True)[0]}
    members = [
        member
        for member in (cmds.sets(src_shading_group, q=True) or [])
        if set(cmds.ls(member.split(".")[0], long=True)) & targets
    ]
    if members:
        cmds.sets(members, edit=True, forceElement=dst_shading_group)


def _rename_network(shading_group, suffix):
    """読み込んだシェーディングのノードを一時ネームスペースから出す（名前が競合したら接尾辞）"""
    for node in _shading_network(shading_group):
        if cmds.objExists(node) and _in_temp_ns(node):
            cmds.rename(node, ":" + _unique_name(_basename(node), suffix))


# ============================================================
# UI
# ============================================================
def _temp_folder():
    """Maya の一時フォルダ"""
    return cmds.internalVar(userTmpDir=True).replace("\\", "/").rstrip("/")


def _option_value(key, default):
    """Maya に保存された設定値（無ければ default）"""
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
    """ツールのウィンドウを開く"""
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    cmds.window(WINDOW, title="{}  v{}".format(TOOL_NAME, __version__), widthHeight=(480, 520))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnAttach=("both", 8))

    # ---- 1. 書き出し ----
    cmds.frameLayout(label="1. Export from the source scene", marginWidth=6, marginHeight=6)
    cmds.radioButtonGrp(
        "smt_saveMode",
        label="Save to",
        numberOfRadioButtons=2,
        labelArray2=["Temp folder", "Custom folder"],
        select=_option_value(OPT_MODE, 1),
        columnWidth3=(90, 120, 140),
        changeCommand=_refresh,
    )
    cmds.textFieldButtonGrp(
        "smt_folder",
        label="Folder",
        buttonLabel="Browse...",
        text=_option_value(OPT_FOLDER, ""),
        adjustableColumn=2,
        columnWidth3=(90, 280, 70),
        buttonCommand=_browse_folder,
        changeCommand=_refresh,
    )
    cmds.textFieldGrp(
        "smt_name",
        label="File name",
        text=_option_value(OPT_NAME, DEFAULT_NAME),
        adjustableColumn=2,
        columnWidth2=(90, 280),
        changeCommand=_refresh,
    )
    cmds.text("smt_exportPath", label="", align="left", font="smallPlainLabelFont")
    cmds.button(label="Export Selected Meshes", height=30, command=_on_export)
    cmds.setParent("..")

    # ---- 2. 読み込み ----
    cmds.frameLayout(label="2. Import into the target scene", marginWidth=6, marginHeight=6)
    cmds.radioButtonGrp(
        "smt_importFrom",
        label="Import from",
        numberOfRadioButtons=2,
        labelArray2=["Last export", "Another file"],
        select=_option_value(OPT_IMPORT_FROM, 1),
        columnWidth3=(90, 120, 140),
        changeCommand=_refresh,
    )
    cmds.textFieldButtonGrp(
        "smt_importFile",
        label="File",
        buttonLabel="Browse...",
        text=_option_value(OPT_IMPORT_FILE, ""),
        adjustableColumn=2,
        columnWidth3=(90, 280, 70),
        buttonCommand=_browse_import_file,
        changeCommand=_refresh,
    )
    cmds.text("smt_importPath", label="", align="left", font="smallPlainLabelFont")

    cmds.separator(height=8, style="in")
    cmds.radioButtonGrp(
        "smt_mode",
        label="If a material exists",
        numberOfRadioButtons=3,
        labelArray3=["Use existing", "Use imported", "Keep both"],
        select=1,
        columnWidth4=(130, 125, 125, 80),
    )
    cmds.radioButtonGrp(
        "smt_parent",
        label="Place meshes under",
        numberOfRadioButtons=3,
        labelArray3=["Matching parent", "Selected node", "World"],
        select=1,
        columnWidth4=(130, 125, 125, 80),
    )
    cmds.radioButtonGrp(
        "smt_clash",
        label="If a name exists",
        numberOfRadioButtons=2,
        labelArray2=["Replace existing", "Keep existing"],
        select=_option_value(OPT_CLASH, 1),
        columnWidth3=(130, 140, 140),
        changeCommand=_refresh,
    )
    cmds.textFieldGrp(
        "smt_suffix",
        label="New suffix",
        text=_option_value(OPT_IMP_SUFFIX, "_imp"),
        columnWidth2=(130, 120),
        changeCommand=_refresh,
    )
    cmds.textFieldGrp(
        "smt_oldSuffix",
        label="Old suffix",
        text=_option_value(OPT_OLD_SUFFIX, "_old"),
        columnWidth2=(130, 120),
        changeCommand=_refresh,
    )
    cmds.checkBoxGrp(
        "smt_opts",
        numberOfCheckBoxes=1,
        label="",
        label1="Keep the original bind pose",
        value1=False,  # 既定はオフ：意図しないメッシュの変形を防ぐ
        columnWidth2=(130, 220),
    )
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

    for control, prefix, get_path in (
        ("smt_exportPath", "Saves to: ", _export_path),
        ("smt_importPath", "Imports from: ", _import_path),
    ):
        try:
            label = prefix + get_path()
        except RuntimeError as error:
            label = "! " + str(error)
        cmds.text(control, edit=True, label=label)

    cmds.optionVar(intValue=(OPT_MODE, save_mode))
    cmds.optionVar(
        stringValue=(OPT_FOLDER, cmds.textFieldButtonGrp("smt_folder", q=True, text=True))
    )
    cmds.optionVar(stringValue=(OPT_NAME, cmds.textFieldGrp("smt_name", q=True, text=True)))
    cmds.optionVar(intValue=(OPT_IMPORT_FROM, import_from))
    if cmds.radioButtonGrp("smt_clash", exists=True):
        cmds.optionVar(intValue=(OPT_CLASH, cmds.radioButtonGrp("smt_clash", q=True, select=True)))
        cmds.optionVar(
            stringValue=(OPT_IMP_SUFFIX, cmds.textFieldGrp("smt_suffix", q=True, text=True))
        )
        cmds.optionVar(
            stringValue=(OPT_OLD_SUFFIX, cmds.textFieldGrp("smt_oldSuffix", q=True, text=True))
        )
    cmds.optionVar(
        stringValue=(OPT_IMPORT_FILE, cmds.textFieldButtonGrp("smt_importFile", q=True, text=True))
    )


def _browse_folder(*_):
    """保存先フォルダを選ぶダイアログ"""
    start = cmds.textFieldButtonGrp("smt_folder", q=True, text=True) or _temp_folder()
    result = cmds.fileDialog2(
        fileMode=3, dialogStyle=2, startingDirectory=start, caption="Choose a folder to save to"
    )
    if result:
        cmds.textFieldButtonGrp("smt_folder", edit=True, text=result[0])
        _refresh()


def _browse_import_file(*_):
    """読み込むファイルを選ぶダイアログ"""
    current = cmds.textFieldButtonGrp("smt_importFile", q=True, text=True)
    start = os.path.dirname(current) if current else _temp_folder()
    result = cmds.fileDialog2(
        fileFilter="Maya ASCII (*.ma)",
        fileMode=1,
        dialogStyle=2,
        startingDirectory=start,
        caption="Choose a file to import",
    )
    if result:
        cmds.radioButtonGrp("smt_importFrom", edit=True, select=2)
        cmds.textFieldButtonGrp("smt_importFile", edit=True, text=result[0].replace("\\", "/"))
        _refresh()


def _on_export(*_):
    """「Export Selected Meshes」ボタンの処理"""
    try:
        path = _export_path()
    except RuntimeError as error:
        cmds.confirmDialog(title="Export", message=str(error), button=["OK"])
        return
    # 任意のフォルダに既存ファイルがあるときだけ上書きを確認（一時フォルダは黙って上書き）
    if cmds.radioButtonGrp("smt_saveMode", q=True, select=True) == 2 and os.path.isfile(path):
        if (
            cmds.confirmDialog(
                title="Export",
                message="File already exists. Overwrite?\n" + path,
                button=["Overwrite", "Cancel"],
                defaultButton="Cancel",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            != "Overwrite"
        ):
            return
    try:
        meshes = export_meshes(path)
    except Exception as error:
        cmds.confirmDialog(title="Export", message=str(error), button=["OK"])
        return
    cmds.confirmDialog(
        title="Export", message="Exported {} mesh(es).\n{}".format(len(meshes), path), button=["OK"]
    )


def _format_offsets(translate, rotate, digits=3):
    """ずれを「tx 0.000  ty 5.341 ...」の形の文字列にする（-0.000 は 0.000 と書く）"""

    def number(value, places):
        text = "{:.{}f}".format(value, places)
        return text[1:] if text.startswith("-") and float(text) == 0 else text

    parts = [
        "{} {}".format(axis, number(v, digits)) for axis, v in zip(("tx", "ty", "tz"), translate)
    ]
    parts += ["{} {}".format(axis, number(v, 2)) for axis, v in zip(("rx", "ry", "rz"), rotate)]
    return "  ".join(parts)


def _confirm_pose_difference(differences):
    """移植先の骨が、元のバインド時の位置と違うときの確認ダイアログ。続けるなら True"""
    shown = differences[:10]
    lines = ["  {}: {}".format(name, _format_offsets(t, r)) for name, _, _, t, r in shown]
    if len(differences) > len(shown):
        lines.append("  ... and {} more".format(len(differences) - len(shown)))
    for name, _, _, t, r in differences:
        print("[{}] POSE DIFF: {}: {}".format(TOOL_NAME, name, _format_offsets(t, r, 4)))
    message = (
        "{} joint(s) differ from the source bind pose (world space):\n{}\n\n"
        "The mesh keeps its shape and is bound to the current pose.\n"
        "If the target is posed, go to the bind pose first.\nContinue?"
    ).format(len(differences), "\n".join(lines))
    answer = cmds.confirmDialog(
        title="Import",
        message=message,
        button=["Continue", "Cancel"],
        defaultButton="Cancel",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    return answer == "Continue"


def _on_import(*_):
    """「Import and Transfer」ボタンの処理"""
    try:
        path = _import_path()
    except RuntimeError as error:
        cmds.confirmDialog(title="Import", message=str(error), button=["OK"])
        return
    new_suffix = cmds.textFieldGrp("smt_suffix", q=True, text=True).strip() or "_imp"
    old_suffix = cmds.textFieldGrp("smt_oldSuffix", q=True, text=True).strip() or "_old"
    clash_mode = (CLASH_RENAME_EXISTING, CLASH_RENAME_IMPORTED)[
        cmds.radioButtonGrp("smt_clash", q=True, select=True) - 1
    ]
    mat_mode = (MAT_USE_EXISTING, MAT_USE_IMPORTED, MAT_KEEP_BOTH)[
        cmds.radioButtonGrp("smt_mode", q=True, select=True) - 1
    ]
    parent_mode = (PARENT_BY_NAME, PARENT_SELECTED, PARENT_WORLD)[
        cmds.radioButtonGrp("smt_parent", q=True, select=True) - 1
    ]
    keep_bind_pose = cmds.checkBoxGrp("smt_opts", q=True, value1=True)
    try:
        done, errors, name_resolver = import_meshes(
            path,
            mat_mode,
            parent_mode,
            keep_bind_pose,
            clash_mode,
            new_suffix,
            old_suffix,
            confirm_pose_difference=_confirm_pose_difference,
        )
    except ImportCancelled:
        return
    except Exception as error:
        cmds.confirmDialog(title="Import", message=str(error), button=["OK"])
        return

    for mesh in done:
        print("[{}] OK   : {}".format(TOOL_NAME, mesh))
    for error in errors:
        print("[{}] FAIL : {}".format(TOOL_NAME, error))
    for old_name, new_name in name_resolver.renamed:
        print("[{}] RENAMED: {} -> {}".format(TOOL_NAME, old_name, new_name))
    for name in name_resolver.not_renamed:
        print("[{}] KEPT (protected): {}".format(TOOL_NAME, name))
    if done:
        cmds.select(done, replace=True)

    msg = "Transferred {} mesh(es).".format(len(done))
    if name_resolver.renamed:
        msg += "\nRenamed {} existing node(s) to *{}.".format(
            len(name_resolver.renamed), name_resolver.old_suffix
        )
    if name_resolver.replaced_materials:
        msg += "\nReplaced {} material(s) with the imported ones.".format(
            len(name_resolver.replaced_materials)
        )
    if name_resolver.not_renamed:
        msg += "\n\nCould not rename (locked, referenced or rig):\n" + "\n".join(
            name_resolver.not_renamed
        )
    if errors:
        msg += "\n\nFailed:\n" + "\n".join(errors)
    cmds.confirmDialog(title="Import", message=msg, button=["OK"])
