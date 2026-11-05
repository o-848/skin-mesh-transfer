# o-848 Skin Mesh Transfer

**Part of o-848 Tools** — by OhashiYa

[日本語版はこちら / Japanese](README_ja.md)

o-848 Skin Mesh Transfer is a Maya tool that copies skinned meshes from one scene into another.
If both scenes use the same skeleton, you can bring over the mesh, its skin weights and its materials in one click.

## No more duplicate materials

When you import a mesh into Maya the usual way, Maya also brings in copies of its materials, even if the target scene already has them. You end up with extra materials such as `skin_mat1`.

This tool avoids that. Before it assigns any material, it checks the target scene. If the scene already has a material with the same name, the tool assigns the mesh to that existing material, face by face, and deletes the imported copy.

- Your scene stays clean, with no duplicate materials or shading groups.
- Any changes you have already made to those materials in the target scene are kept.
- Meshes with several materials work too. Every face keeps the material it had.

The tool does this by default. You can also replace the existing materials with the imported ones, or keep both to compare them.

## Features

- The tool reuses existing materials with the same name (see above).
- You can transfer several meshes at once. Select the meshes themselves, or simply select their group: the tool finds every skinned mesh inside it and brings the group along.
- The tool exports only what it needs: the meshes, their skin, their joints and their materials. It leaves rig controls behind.
- The tool binds each mesh to the joints in the target scene that have the same names.
- The tool copies skin weights exactly, vertex by vertex.
- If the target scene is missing any joints, the tool stops and shows you which ones.
- If a name is already taken in the target scene, you choose which side gets renamed: the imported node (`_imp`) or the existing one (`_old`).
- When the transfer is done, the tool deletes everything it imported temporarily: the source joints, the source meshes and any unused materials.

## Requirements

| | |
|---|---|
| Maya | Tested on Maya 2023 and 2026. Maya 2024 and 2025 should also work, but have not been tested yet. |
| OS | Tested on Windows. |

## Installation

### Drag and drop (recommended)

1. Open the [latest release](https://github.com/o-848/skin-mesh-transfer/releases/latest) and download `o848_SkinMeshTransfer_vX.X.X.zip` from the **Assets** section.
   - Download the whole zip file. The installer needs the other files in the same folder.
2. Unzip the file.
3. Drag `install.mel` and drop it into the Maya viewport.
4. The installer adds a button to the **o848** shelf tab.

The installer copies the tool to `Documents/maya/scripts`, so every version of Maya on your computer can use it.
The installer does not change your `Maya.env` or `userSetup.py` files.

### Manual installation

1. Copy `scripts/o848_skin_mesh_transfer.py` to `Documents/maya/scripts`.
2. Restart Maya, then run the following code in the Script Editor (Python tab):

```python
import o848_skin_mesh_transfer
o848_skin_mesh_transfer.show_ui()
```

## How to use

1. **In the source scene**, select the skinned meshes (or their group) and click **Export Selected Meshes**.
2. **In the target scene**, click **Import and Transfer**.

Undo (Ctrl+Z) does not work for the import. If you need to undo it, delete the transferred meshes.

### 1. Export from the source scene

| Option | What it does |
|---|---|
| Save to: Temp folder | The tool saves the file in Maya's temp folder. Each export replaces the previous file. This is the default. |
| Save to: Custom folder | You choose the folder and the file name. Use this option if you want to keep the file or give it to someone else. |

The line under the options shows exactly where the file will be saved.

### 2. Import into the target scene

| Option | What it does |
|---|---|
| Import from: Last export | The tool imports the file you exported last, using the location set in section 1. This is the default. |
| Import from: Another file | Click **Browse...** and choose any `.ma` file exported by this tool, for example a file from a custom folder or from someone else. |
| If a material exists: Use existing | If the scene already has a material with the same name, the imported mesh uses that existing material, and the imported material is discarded. This is the default. |
| If a material exists: Use imported | The imported material replaces the existing one. Every mesh that used the existing material, including the old mesh, now uses the imported material, and the existing material is deleted. |
| If a material exists: Keep both | The tool keeps both materials and adds the new suffix to the imported one (for example, `skin_mat` and `skin_mat_imp`). Use this to compare old and new. |
| Place meshes under: Matching parent | The tool looks for a node with the same name as the mesh's original parent and places the mesh under it. If there is no such node, the mesh goes to the top level of the scene. This is the default. If you exported a group, this option applies to the group. |
| Place meshes under: Selected node | The tool places the mesh under the node you have selected when you click **Import and Transfer**. Select exactly one node. |
| Place meshes under: World | The tool places the mesh at the top level of the scene. |
| If a name exists: Replace existing | The imported node takes over the name, and the tool adds the old suffix to the existing node (for example, `body_old` and `body`). The existing node is not deleted. This is the default. |
| If a name exists: Keep existing | The existing node keeps its name, and the tool adds the new suffix to the imported node (for example, `body` and `body_imp`). |
| New suffix | Added to the imported node when you choose **Keep existing**. The default is `_imp`. |
| Old suffix | Added to the existing node when you choose **Replace existing**. The default is `_old`. |
| Keep the original bind pose | Off by default. When it is off, the mesh keeps its shape and is bound to the target joints as they are now, so put the target skeleton in its bind pose before you import. When it is on, the tool copies the original bind pose: the mesh binds correctly even if the target skeleton is in a pose, but if the target joints are in different positions, the mesh moves and deforms to follow them. |

### Importing a file from a custom folder

1. In the **Import into the target scene** section, select **Another file**.
2. Click **Browse...** and choose the `.ma` file.
3. Click **Import and Transfer**.

## Notes and limitations

- When **Keep the original bind pose** is off, the tool compares the target joints with the joint positions stored in the exported file (the source bind pose). If any joint differs, the tool lists the joints and asks whether to continue. If the target is posed, cancel, go to the bind pose, and import again.
- When you export a group, the tool recreates the group in the target scene. If a group with the same name already exists in the same place, the tool puts the meshes into that group instead of creating a new one.
- With "Replace existing", the tool never renames joints, groups that contain joints, or locked or referenced nodes. In those cases it renames the imported node instead and lists it in the result.
- The material option works separately from the name option, so you can, for example, take the new mesh but keep the existing materials.
- With "Replace existing", or when the tool creates a group, the tool keeps the node order of the source scene. This keeps the node order in exported FBX files stable.
- "Use imported" deletes the existing material node, but not the textures connected to it. Use **File > Optimize Scene Size** to remove unused nodes.

- The tool matches joints by name. If two joints have the same name, it shows an error.
- If the target scene has extra joints that the source mesh does not use (for example, extra jiggle joints), the tool does not add them to the skin.
- Deformers that come before the skin, such as blendShapes, are baked into the mesh shape.
- Delete the modeling history of your meshes before you export them.

## For reviewers and administrators

- The tool does not use AI while it runs, and it does not connect to any AI service.
- The tool does not access the network. It never sends scene data outside your computer.
- The tool writes files only in these places:
  - Export files: Maya's temp folder, or the folder you choose.
  - Installation: a copy of the tool in `Documents/maya/scripts`, and the **o848** shelf (Maya saves it in its preferences folder).
  - Tool settings (such as the save location) are stored in Maya's preferences (optionVar).
- The tool does not change `Maya.env` or `userSetup.py`.
- All source code is public. The tool itself is a single Python file.

## Version history

- **1.2.0** — When you export a group, the group is now imported too. A new option lets you replace an existing node with the same name: the imported node takes over the name, and the existing node is kept as `_old`. The material option now has three choices: use the existing material, use the imported material, or keep both. The node order of the source scene is kept. Fixed a bug that added `_imp` even when there was no name clash.
- **1.1.0** — You can now select a group to export all the skinned meshes inside it. A new option lets you choose where the meshes go: under the matching parent, under the selected node, or at the top level of the scene. You can now import any exported file with "Import from: Another file". The UI text has been rewritten to be clearer. The unsaved-scene warning has been removed.
- **1.0.0** — First release.

## License

This tool is released under the MIT License. See [LICENSE](LICENSE) for details.

### Modifying and redistributing

If you modify or redistribute this tool, you must keep the copyright notice (`Copyright (c) 2026 OhashiYa`) and the LICENSE file, as the MIT License requires.

We also ask that you credit the original tool name (o-848 Skin Mesh Transfer), the author (OhashiYa), and the URL of this repository.
Please do not present this tool, or a modified version of it, as your own original work.
