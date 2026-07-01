# Register Unreal Editor menu entries for LM Studio metadata export.
#
# Usage (Editor Python):
#   exec(open(r'path/to/tools/ue_export/register_export_menu.py', encoding='utf-8').read())
#   register_lmstudio_export_menu(r'C:\UnrealExports', content_path='/Game')

import os


def _tools_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _python_command(script_name: str, function_name: str, export_dir: str, content_path: str) -> str:
    script_path = os.path.join(_tools_dir(), script_name).replace("\\", "/")
    export_dir = export_dir.replace("\\", "/")
    content_path = content_path.replace("\\", "/")
    return (
        f"exec(open(r'{script_path}', encoding='utf-8').read()); "
        f"{function_name}(r'{export_dir}', content_path=r'{content_path}')"
    )


def register_lmstudio_export_menu(export_dir: str, content_path: str = "/Game") -> None:
    import unreal

    menus = unreal.ToolMenus.get()
    menu_name = "LevelEditor.MainMenu.LMStudioMetadata"
    existing = menus.find_menu(menu_name)
    if existing:
        menus.remove_menu(existing)

    lm_menu = menus.extend_menu("LevelEditor.MainMenu")
    section = lm_menu.add_section("LMStudioMetadata", "LM Studio")

    entries = [
        ("Export All Metadata", "run_all_exports.py", "run_all_metadata_exports"),
        ("Export Materials Only", "run_all_exports.py", "export_materials_only"),
        ("Export Blueprints Only", "run_all_exports.py", "export_blueprints_only"),
    ]
    for label, script_name, function_name in entries:
        entry = unreal.ToolMenuEntry(
            name=f"LMStudio.{label.replace(' ', '')}",
            type=unreal.MultiBlockType.MENU_ENTRY,
        )
        entry.set_label(label)
        entry.set_string_command(
            unreal.ToolMenuStringCommandType.PYTHON,
            custom_type_name="",
            string=_python_command(script_name, function_name, export_dir, content_path),
        )
        section.add_menu_entry(label, entry)

    menus.refresh_all_widgets()

    watcher_path = os.path.join(_tools_dir(), "export_request_watcher.py")
    watcher_cmd = (
        f"exec(open(r'{watcher_path}', encoding='utf-8').read()); "
        f"start_export_request_watcher(r'{export_dir}', r'{_tools_dir()}')"
    )
    namespace = {}
    with open(watcher_path, encoding="utf-8") as handle:
        exec(handle.read(), namespace)
    namespace["start_export_request_watcher"](export_dir, _tools_dir())

    unreal.log(
        f"LM Studio metadata menu + export watcher registered. export_dir={export_dir} content_path={content_path}"
    )
