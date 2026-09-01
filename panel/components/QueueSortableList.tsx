"use client";

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useState } from "react";

export type QueueSong = {
  id?: string;
  index?: number;
  name?: string;
  artists?: string;
  album?: string;
  cover?: string;
  platform?: string;
  duration?: number | string;
  durationText?: string;
  clientKey?: string;
};

type Props = {
  songs: QueueSong[];
  connected: boolean;
  busy: boolean;
  removing: number | null;
  onDragStart: () => void;
  onDragCancel: () => void;
  onMove: (source: number, target: number) => void;
  onRemove: (position: number) => void;
};

function durationSeconds(value: unknown): number {
  if (typeof value === "number") return Math.max(0, value);
  const text = String(value || "").trim();
  if (!text) return 0;
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(":").map(Number);
  if (parts.some((part) => !Number.isFinite(part))) return 0;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

function formatTime(value: number): string {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function SortableRow({
  song,
  index,
  dragDisabled,
  actionDisabled,
  removing,
  onRemove,
}: {
  song: QueueSong;
  index: number;
  dragDisabled: boolean;
  actionDisabled: boolean;
  removing: boolean;
  onRemove: () => void;
}) {
  const id = String(song.clientKey);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id, disabled: dragDisabled });
  return (
    <div
      ref={setNodeRef}
      className={`queue-row ${isDragging ? "dragging" : ""}`}
      data-queue-position={index}
      style={{ transform: CSS.Transform.toString(transform), transition }}
    >
      <button
        className="queue-drag"
        type="button"
        aria-label={`移动第 ${index + 1} 首：${song.name || "未知歌曲"}`}
        aria-keyshortcuts="Space ArrowUp ArrowDown"
        disabled={dragDisabled}
        {...attributes}
        {...listeners}
      >
        ⠿
      </button>
      <span className="queue-index">{String(index + 1).padStart(2, "0")}</span>
      <div className="mini-cover">
        {song.cover ? <img src={String(song.cover).replace(/^http:/, "https:")} alt="" /> : "♫"}
      </div>
      <div className="queue-song"><strong>{song.name}</strong><span>{song.artists}</span></div>
      <span className="source-tag">{song.platform === "netease" ? "网易云" : "QQ 音乐"}</span>
      <span className="duration">{song.durationText || formatTime(durationSeconds(song.duration))}</span>
      <button className="queue-remove" type="button" onClick={onRemove} disabled={actionDisabled}>
        {removing ? "删除中" : "删除"}
      </button>
    </div>
  );
}

export default function QueueSortableList(props: Props) {
  const [active, setActive] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  if (!props.connected) return <div className="empty-state">播放队列数据不可用。</div>;
  if (!props.songs.length) return <div className="empty-state">待播队列为空。</div>;
  const ids = props.songs.map((song) => String(song.clientKey));

  const finish = (event: DragEndEvent) => {
    const source = ids.indexOf(String(event.active.id));
    const target = event.over ? ids.indexOf(String(event.over.id)) : -1;
    setActive(null);
    if (source >= 0 && target >= 0 && source !== target) props.onMove(source, target);
    else props.onDragCancel();
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={(event) => {
        setActive(String(event.active.id));
        props.onDragStart();
      }}
      onDragCancel={() => {
        setActive(null);
        props.onDragCancel();
      }}
      onDragEnd={finish}
    >
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        {props.songs.map((song, index) => (
          <SortableRow
            key={String(song.clientKey)}
            song={song}
            index={index}
            dragDisabled={props.busy || props.songs.length < 2}
            actionDisabled={props.busy || active !== null}
            removing={props.removing === index + 1}
            onRemove={() => props.onRemove(index + 1)}
          />
        ))}
      </SortableContext>
    </DndContext>
  );
}
