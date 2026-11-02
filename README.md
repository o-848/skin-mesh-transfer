# o-848 Skin Mesh Transfer

**part of o-848 Tools** — by OhashiYa

[日本語版はこちら / Japanese](README_ja.md)

A Maya tool that moves skinned meshes from one scene to another.
When both scenes use the same skeleton, the mesh, its skin weights and its materials are transferred in one click.

## Features

- Exports only what is needed: the mesh, its skin, its joints and its materials. Rig controls are not included.
- Binds the mesh to the joints of the target scene, matched by name.
- Copies skin weights exactly, vertex by vertex.
- Stops and lists the joint names if any joint is missing in the target scene.
- Materials: uses an existing material with the same name, or keeps the imported one with a suffix.
- Adds a suffix only to nodes whose names conflict.
- Removes all imported temporary nodes (joints, source mesh, unused materials) after the transfer.

## Requirements

| | |
|---|---|
| Maya | 2023 (tested). 2024 – 2026 support is planned. |
| OS | Windows (tested) |

## Installation

### Drag and drop (recommended)

1. Unzip the downloaded file.
2. Drag and drop `install.mel` into the Maya viewport.
3. A button is added to the **o848** shelf tab.

The tool is copied to `Documents/maya/scripts`, so it works in every Maya version on the same PC.
The installer does not change `Maya.env` or `userSetup.py`.

### Manual

1. Copy `scripts/o848_skin_mesh_transfer.py` into `Documents/maya/scripts`.
2. Restart Maya and run this in the Script Editor (Python tab):

```python
import o848_skin_mesh_transfer
o848_skin_mesh_transfer.show_ui()
```

## Usage

1. **Source scene**: select the skinned mesh transforms and click **Export Selected Meshes**.
2. **Target scene**: click **Import and Transfer**.

Save your scene before importing. The import cannot be undone.

### File Location

| Option | Description |
|---|---|
| Temp folder (default) | Uses Maya's temp folder. The file is overwritten each time. |
| Custom folder | Choose any folder and file name. Use this to keep the file or to share it with others. |
| Select Existing File... | Picks an existing `.ma` file to import. |

### Import options

| Option | Description |
|---|---|
| Conflict suffix | Added only to nodes whose names already exist. Default: `_imp` |
| Same-name material: Use existing | Assigns the existing material with the same name. |
| Same-name material: Keep with suffix | Keeps the imported material and adds the suffix. |
| Match parent by name | Puts the mesh under a node with the same name as its original parent. If none is found, the mesh goes to the world. |
| Copy bind matrices | Copies the original bind pose. The mesh binds correctly even when the target skeleton is posed. Turn this off if the target skeleton uses a different bind pose. |

## Notes and limitations

- Joints are matched by name. An error is shown if two joints share the same name.
- Joints that exist only in the target scene (for example, extra jiggle joints) are not added to the skin.
- Deformers before the skin (such as blendShapes) are baked into the mesh shape.
- Delete modeling history before exporting.

## Version history

- **1.0.0** — First release

## License

MIT License. See [LICENSE](LICENSE).
