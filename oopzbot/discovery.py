"""OOPZ area and channel discovery formatting."""

from __future__ import annotations


def discovery_payload(runtime, area_id: str = "", areas_only: bool = False) -> dict:
    if area_id:
        areas = [{"id": area_id, "name": ""}]
    else:
        areas = runtime.get_joined_areas()
    result = []
    for area in areas:
        current_id = str(area.get("id") or area.get("area_id") or area.get("areaId") or "")
        item = {
            "id": current_id,
            "name": str(area.get("name") or ""),
            "groups": [],
        }
        if not areas_only and current_id:
            item["groups"] = runtime.get_area_channels(current_id)
        result.append(item)
    return {"areas": result}


def print_discovery(payload: dict) -> None:
    areas = payload.get("areas") or []
    if not areas:
        print("当前账号没有加入任何 OOPZ 域。")
        return
    for area in areas:
        area_id = str(area.get("id") or "")
        print(f"\n域：{area.get('name') or '未命名'}\n  ID: {area_id}")
        for group in area.get("groups") or []:
            print(f"  分组：{group.get('name', '未命名')}")
            for channel in group.get("channels") or []:
                print(
                    f"    - {channel.get('name', '未命名')} "
                    f"[{channel.get('type', 'UNKNOWN')}] {channel.get('id', '')}"
                )
