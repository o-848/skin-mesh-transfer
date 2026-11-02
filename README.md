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

The tool does this by default. If you prefer, you can keep the imported materials instead; the tool then adds a suffix to their names.

## Features

- The tool reuses existing materials with the same name (see above).
- You can transfer several meshes at once. Select the meshes themselves, or simply select their group: the tool finds every skinned mesh inside it.
- The tool exports only what it needs: the meshes, their skin, their joints and their materials. It leaves rig controls behind.
- The tool binds each mesh to the joints in the target scene that have the same names.
- The tool copies skin weights exactly, vertex by vertex.
- If the target scene is missing any joints, the tool stops and shows you which ones.
- The tool renames a node only when its name is already taken in the target scene.
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

Save your scene before you import. You cannot undo the import.

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
| If a material exists: Use the existing one | If the scene already has a material with the same name, the tool assigns the mesh to it. This is the default. |
| If a material exists: Import a copy | The tool keeps the imported material and adds the suffix to its name. |
| Place meshes under: Matching parent | The tool looks for a node with the same name as the mesh's original parent and places the mesh under it. If there is no such node, the mesh goes to the top level of the scene. This is the default. |
| Place meshes under: Selected node | The tool places the mesh under the node you have selected when you click **Import and Transfer**. Select exactly one node. |
| Place meshes under: World | The tool places the mesh at the top level of the scene. |
| Suffix for name clashes | The tool adds this suffix only to nodes whose names already exist in the scene. The default is `_imp`. |
| Keep the original bind pose | The tool copies the original bind pose, so the mesh binds correctly even if the target skeleton is in a pose. Turn this off if the target skeleton has a different bind pose. |

### Importing a file from a custom folder

1. In the **Import into the target scene** section, select **Another file**.
2. Click **Browse...** and choose the `.ma` file.
3. Click **Import and Transfer**.

## Notes and limitations

- The tool matches joints by name. If two joints have the same name, it shows an error.
- If the target scene has extra joints that the source mesh does not use (for example, extra jiggle joints), the tool does not add them to the skin.
- Deformers that come before the skin, such as blendShapes, are baked into the mesh shape.
- Delete the modeling history of your meshes before you export them.

## Version history

- **1.1.0** — You can now select a group to export all the skinned meshes inside it. A new option lets you choose where the meshes go: under the matching parent, under the selected node, or at the top level of the scene. You can now import any exported file with "Import from: Another file". The UI text has been rewritten to be clearer.
- **1.0.0** — First release.

## License

This tool is released under the MIT License. See [LICENSE](LICENSE) for details.
