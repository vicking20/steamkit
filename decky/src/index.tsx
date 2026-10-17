import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  ModalRoot,
  DropdownItem,
  Field,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import {
  FileSelectionType,
  callable,
  definePlugin,
  openFilePicker,
  routerHook,
  toaster,
} from "@decky/api";
import { useEffect, useState } from "react";
import { FaGamepad } from "react-icons/fa";

declare const SteamClient: any;

type Mode = "restart" | "live";
type Settings = {
  sgdb_key: string;
  compat_tool: string;
  last_dir: string;
  mode: Mode;
  gaming_mode: boolean;
  shared_saves: boolean;
  include_desktop_wine: boolean;
};
type Backup = { name: string; size: number; time: number };
type SavesInfo = { root: string; games: { label: string; exists: boolean; linked: boolean }[]; backups: Backup[] };
type LinkResult = {
  disabled?: boolean;
  linked?: number;
  moved?: number;
  conflicts?: number;
  waiting?: number;
  running?: number;
  notes?: string[];
};
type LanInfo = {
  enabled: boolean;
  running: boolean;
  name: string;
  pairing_open: boolean;
  tracked: string[];
  peers: { id: string; name: string }[];
};
type LanPeer = { id: string; name: string; host: string; port: number; paired: boolean };
type LanFolder = { entry: string; files: number; size: number; mtime: number; likely: boolean; where: "here" | "there" | "both" | "none" };
type LanSyncResult = {
  ok: boolean;
  error?: string;
  pulled?: number;
  pushed?: number;
  overwritten?: number;
  conflicts?: string[];
  backup?: string | null;
};
type Drive = {
  name: string;
  uuid: string;
  fstype: string;
  label: string;
  size: string;
  mountpoint: string | null;
  configured: boolean;
  suggested: string;
};
type GameExe = { path: string; already: boolean };
type Game = { name: string; exes: GameExe[] };
type Location = { path: string; label: string };
type Tool = { id: string; label: string; installed: boolean };
type Art = {
  found: boolean;
  error?: string;
  icon?: string;
  name?: string;
  matched?: string;
  reason?: string;
  assets?: { type: number; ext: string; data: string }[];
};
type Added = { appid: number; name: string; exe: string; user: string };
type Job = {
  state: "idle" | "running" | "done" | "error";
  step?: string;
  done?: number;
  total?: number;
  added?: number;
  skipped?: number;
  removed?: number;
  notes?: string[];
  error?: string;
};

const getSettings = callable<[], Settings>("get_settings");
const setSetting = callable<[string, any], void>("set_setting");
const listDrives = callable<[], Drive[]>("list_drives");
const mountDrive = callable<[string, string], { ok: boolean; line?: string; error?: string }>("mount_drive");
const scan = callable<[string], Game[]>("scan");
const listLocations = callable<[], Location[]>("list_locations");
const listCompatTools = callable<[], Tool[]>("list_compat_tools");
const fetchArt = callable<[string, string, number], Art>("fetch_art");
const searchArt = callable<[string], { hits: { id: number; name: string; year: string }[]; error?: string }>("search_art");
const artById = callable<[number, number], Art>("art_by_id");
const recordAdded = callable<[number, string, string, string], void>("record_added");
const listAdded = callable<[], Added[]>("list_added");
const forget = callable<[number], void>("forget");
const applyGames = callable<[string, { name: string; exe: string }[], string], { ok: boolean; error?: string }>("apply_games");
const removeGames = callable<[number[]], { ok: boolean; error?: string }>("remove_games");
const jobStatus = callable<[], Job>("job_status");
const clearJob = callable<[], void>("clear_job");
const logError = callable<[string], void>("log_error");
const savesInfo = callable<[], SavesInfo>("saves_info");
const linkSaves = callable<[], LinkResult>("link_saves");
const backupSaves = callable<[], { ok: boolean; name?: string; size?: number; error?: string }>("backup_saves");
const restoreSaves = callable<[string], { ok: boolean; safety?: string; error?: string }>("restore_saves");
const setSavesParent = callable<[string], { ok: boolean; root?: string; error?: string }>("set_saves_parent");

const lanInfo = callable<[], LanInfo>("lan_info");
const setLan = callable<[boolean], { ok: boolean; error?: string }>("set_lan");
const lanSetName = callable<[string], void>("lan_set_name");
const lanPairCode = callable<[], { ok: boolean; code?: string; seconds?: number; error?: string }>("lan_pair_code");
const lanScan = callable<[], LanPeer[]>("lan_scan");
const lanPair = callable<[string, number, string], { ok: boolean; name?: string; error?: string }>("lan_pair");
const lanUnpair = callable<[string], void>("lan_unpair");
const lanFolders = callable<[string], { folders: LanFolder[]; tracked: string[]; error: string }>("lan_folders");
const lanSetTracked = callable<[string[]], { ok: boolean; error?: string }>("lan_set_tracked");
const lanSync = callable<[string, string], LanSyncResult>("lan_sync");

const ADD_ROUTE = "/steamkit/add";
const MANAGE_ROUTE = "/steamkit/manage";
const RESTORE_ROUTE = "/steamkit/restore";
const SYNC_ROUTE = "/steamkit/sync";
const fmtSize = (b: number) => (b > 1 << 20 ? `${(b / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(b / 1024)} KB`);

function summarizeLink(r: LinkResult): string {
  const parts = [`${r.linked ?? 0} linked`];
  if (r.moved) parts.push(`${r.moved} save item(s) moved in`);
  if (r.conflicts) parts.push(`${r.conflicts} kept side by side`);
  if (r.waiting) parts.push(`${r.waiting} not launched yet`);
  if (r.running) parts.push(`${r.running} running (skipped)`);
  return parts.join(", ");
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const dirOf = (p: string) => p.substring(0, p.lastIndexOf("/"));
const baseName = (p: string) => p.substring(p.lastIndexOf("/") + 1);

let pending: { dir: string; games: Game[] } = { dir: "", games: [] };

// userdata folder id (SteamID3), matching what the CLI records
function currentUser(): string {
  const sid = window.App?.m_CurrentUser?.strSteamID;
  return sid ? String(BigInt(sid) - 76561197960265728n) : "";
}

// Shows the result of a restart-mode job once Steam (and this plugin's UI) is back.
async function reportFinishedJob() {
  const job = await jobStatus();
  if (job.state === "done") {
    const msg =
      job.removed !== undefined
        ? `Removed ${job.removed} game(s)`
        : `Added ${job.added} game(s)${job.skipped ? `, ${job.skipped} already present` : ""}`;
    toaster.toast({ title: "SteamKit", body: msg });
    (job.notes ?? []).slice(0, 5).forEach((n) => toaster.toast({ title: "SteamKit", body: n }));
    await clearJob();
  } else if (job.state === "error") {
    toaster.toast({ title: "SteamKit failed", body: job.error ?? "Unknown error" });
    await clearJob();
  }
}

async function followJob(onStatus: (s: string) => void) {
  for (;;) {
    const job = await jobStatus();
    if (job.state !== "running") return reportFinishedJob();
    onStatus(job.total ? `${job.step} (${job.done}/${job.total})` : job.step ?? "Working…");
    await sleep(700);
  }
}

async function setArt(appid: number, art: Art) {
  for (const a of art.assets ?? []) {
    await SteamClient.Apps.SetCustomArtworkForApp(appid, a.data, a.ext, a.type);
  }
  if (art.icon) SteamClient.Apps.SetShortcutIcon(appid, art.icon);
}

async function applyArt(appid: number, name: string, exe: string): Promise<string> {
  try {
    const art = await fetchArt(name, exe, appid);
    if (art.found) {
      await setArt(appid, art);
      return "";
    }
    if (art.reason === "error") return ` (${art.error})`;
    return art.reason === "no_key" ? " (no SteamGridDB key)" : " (no artwork found)";
  } catch (e) {
    logError(`artwork for ${name}: ${e}`);
    return " (artwork failed)";
  }
}

async function addToSteam(name: string, exe: string, tool: string, onStatus: (s: string) => void) {
  onStatus("Adding shortcut…");
  const appid: number = await SteamClient.Apps.AddShortcut(name, exe, dirOf(exe), "");
  await sleep(300); // Steam needs a moment before the shortcut accepts edits
  SteamClient.Apps.SetShortcutName(appid, name);
  SteamClient.Apps.SetShortcutStartDir(appid, `"${dirOf(exe)}"`);
  if (tool) SteamClient.Apps.SpecifyCompatTool(appid, tool);

  onStatus("Fetching artwork…");
  const artNote = await applyArt(appid, name, exe);

  await recordAdded(appid, currentUser(), name, exe);
  return artNote;
}

function AddGamesPage() {
  const [games] = useState<Game[]>(pending.games);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState(false);
  const [tools, setTools] = useState<Tool[]>([]);
  const [tool, setTool] = useState("proton_experimental");
  const [mode, setMode] = useState<Mode>("restart");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    listCompatTools().then(setTools);
    getSettings().then((s) => {
      setTool(s.compat_tool);
      setMode(s.mode);
    });
  }, []);

  const isAdded = (e: GameExe) => e.already || added[e.path];
  const visible = (g: Game) => (showAll ? g.exes : g.exes.slice(0, 1));
  const selectable = games.flatMap((g) => visible(g).filter((e) => !isAdded(e)).map((e) => ({ g, e })));
  const count = Object.keys(selected).length;
  const allOn = selectable.length > 0 && selectable.every(({ e }) => selected[e.path] !== undefined);

  // Default name is the game folder; a second exe from the same game gets its file name appended.
  const defaultName = (g: Game, e: GameExe) => (e === g.exes[0] ? g.name : `${g.name} (${baseName(e.path).replace(/\.exe$/i, "")})`);

  const toggle = (g: Game, e: GameExe, on: boolean) => {
    const next = { ...selected };
    if (on) next[e.path] = defaultName(g, e);
    else delete next[e.path];
    setSelected(next);
  };

  const toggleAll = () => {
    if (allOn) return setSelected({});
    const next: Record<string, string> = {};
    selectable.forEach(({ g, e }) => (next[e.path] = selected[e.path] ?? defaultName(g, e)));
    setSelected(next);
  };

  const rename = (path: string) => {
    let name = selected[path];
    showModal(
      <ConfirmModal strTitle="Name in Steam" onOK={() => setSelected((s) => ({ ...s, [path]: name.trim() || s[path] }))}>
        <TextField label="Name" defaultValue={name} onChange={(ev) => (name = ev.target.value)} />
      </ConfirmModal>,
    );
  };

  const run = async () => {
    setBusy(true);
    await setSetting("compat_tool", tool);
    const todo = Object.entries(selected).map(([exe, name]) => ({ exe, name }));
    if (mode === "restart") {
      const r = await applyGames(currentUser(), todo, tool);
      if (!r.ok) {
        toaster.toast({ title: "SteamKit", body: r.error ?? "Failed" });
        setBusy(false);
        return;
      }
      return followJob(setStatus);
    }
    let ok = 0;
    const done: Record<string, boolean> = {};
    const failed: string[] = [];
    const notes: string[] = [];
    for (const [i, t] of todo.entries()) {
      try {
        const note = await addToSteam(t.name, t.exe, tool, (s) => setStatus(`${i + 1}/${todo.length} ${t.name}: ${s}`));
        done[t.exe] = true;
        ok++;
        if (note) notes.push(t.name + note);
      } catch (e) {
        failed.push(t.name);
        logError(`adding ${t.name} (${t.exe}): ${e}`);
      }
    }
    setAdded({ ...added, ...done });
    setSelected({});
    const summary = `Added ${ok} of ${todo.length}${failed.length ? ` · failed: ${failed.slice(0, 3).join(", ")}` : ""}`;
    toaster.toast({ title: "SteamKit", body: summary });
    setStatus(summary + (notes.length ? ` · ${notes.length} without matching art` : ""));
    setBusy(false);
  };

  const exeCount = games.reduce((n, g) => n + g.exes.length, 0);

  return (
    <div style={{ marginTop: 40, height: "calc(100% - 40px)", overflowY: "scroll", padding: "0 24px 80px" }}>
      <PanelSection title={`${games.length} games, ${exeCount} exes in ${pending.dir}`}>
        <PanelSectionRow>
          <DropdownItem
            label="Compatibility tool"
            rgOptions={tools.map((t) => ({ data: t.id, label: t.label }))}
            selectedOption={tool}
            onChange={(o) => setTool(o.data)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Show all exes"
            description={showAll ? "All exes" : "Main exe only"}
            checked={showAll}
            onChange={setShowAll}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={toggleAll} disabled={busy || selectable.length === 0}>
            {allOn ? "Select none" : "Select all shown"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={run} disabled={busy || count === 0}>
            {busy
              ? status
              : count === 0
                ? "Select games below"
                : mode === "restart"
                  ? `Apply ${count} & restart Steam`
                  : `Add ${count} to Steam`}
          </ButtonItem>
        </PanelSectionRow>
        {!busy && status && (
          <PanelSectionRow>
            <Field label={status} />
          </PanelSectionRow>
        )}
      </PanelSection>

      {games.map((g) => (
        <PanelSection key={g.name} title={showAll ? g.name : undefined}>
          {visible(g).map((e, i) => (
            <div key={e.path}>
              <PanelSectionRow>
                <ToggleField
                  label={showAll ? baseName(e.path) + (i === 0 ? "  ★" : "") : g.name}
                  description={
                    isAdded(e)
                      ? "Already added"
                      : showAll
                        ? e.path.replace(pending.dir + "/", "")
                        : baseName(e.path) + (g.exes.length > 1 ? `  +${g.exes.length - 1}` : "")
                  }
                  checked={selected[e.path] !== undefined}
                  disabled={busy || !!isAdded(e)}
                  onChange={(v) => toggle(g, e, v)}
                />
              </PanelSectionRow>
              {selected[e.path] !== undefined && (
                <PanelSectionRow>
                  <ButtonItem layout="inline" label={`Steam name: ${selected[e.path]}`} onClick={() => rename(e.path)}>
                    Rename
                  </ButtonItem>
                </PanelSectionRow>
              )}
            </div>
          ))}
        </PanelSection>
      ))}
    </div>
  );
}

function ManagePage() {
  const [games, setGames] = useState<Added[]>([]);
  const [selected, setSelected] = useState<Record<number, boolean>>({});
  const [mode, setMode] = useState<Mode>("restart");
  const [status, setStatus] = useState("");
  const refresh = () => listAdded().then((g) => setGames(g.sort((a, b) => a.name.localeCompare(b.name))));
  useEffect(() => {
    refresh();
    getSettings().then((s) => setMode(s.mode));
  }, []);

  const chosen = games.filter((g) => selected[g.appid]);
  const allOn = games.length > 0 && chosen.length === games.length;
  const [missing, setMissing] = useState<string[]>([]);

  const findArt = (g: Added) => {
    let term = g.name;
    showModal(
      <ConfirmModal strTitle="Search artwork" strOKButtonText="Search" onOK={() => pickArt(g, term)}>
        <TextField label="Game name" defaultValue={term} onChange={(e) => (term = e.target.value)} />
      </ConfirmModal>,
    );
  };

  const pickArt = async (g: Added, term: string) => {
    const { hits, error } = await searchArt(term);
    if (hits.length === 0) {
      toaster.toast({ title: "SteamKit", body: error ?? `No results for "${term}"` });
      return;
    }
    const modal = showModal(
      <ModalRoot onCancel={() => modal.Close()}>
        <div style={{ fontWeight: "bold", marginBottom: 12 }}>Pick the right game</div>
        {hits.map((h) => (
          <DialogButton
            key={h.id}
            style={{ marginBottom: 8 }}
            onClick={async () => {
              modal.Close();
              setStatus(`Artwork: ${h.name}`);
              const art = await artById(h.id, g.appid);
              await setArt(g.appid, art);
              setStatus("");
              setMissing((m) => m.filter((x) => !x.startsWith(g.name)));
              toaster.toast({ title: "SteamKit", body: art.found ? `Artwork set: ${h.name}` : art.error ?? "No artwork for that one" });
            }}
          >
            {h.name}
            {h.year ? ` (${h.year})` : ""}
          </DialogButton>
        ))}
      </ModalRoot>,
    );
  };

  const getArt = async () => {
    const miss: string[] = [];
    let fatal = "";
    for (const [i, g] of chosen.entries()) {
      setStatus(`Artwork ${i + 1}/${chosen.length}: ${g.name}`);
      const note = await applyArt(g.appid, g.name, g.exe);
      if (/key|reach|error/i.test(note)) {
        fatal = note.replace(/^ \(|\)$/g, "");
        break;
      }
      if (note) miss.push(g.name + note);
    }
    setStatus("");
    setMissing(miss);
    setSelected({});
    if (fatal) {
      toaster.toast({ title: "SteamKit", body: fatal });
      return;
    }
    toaster.toast({ title: "SteamKit", body: `Artwork: ${chosen.length - miss.length}/${chosen.length}. Restart Steam for icons.` });
  };

  const remove = () =>
    showModal(
      <ConfirmModal
        strTitle={`Remove ${chosen.length} game(s)?`}
        strDescription={"Game files stay." + (mode === "restart" ? " Steam restarts." : "")}
        strOKButtonText="Remove"
        onOK={async () => {
          if (mode === "restart") {
            const r = await removeGames(chosen.map((g) => g.appid));
            if (r.ok) return followJob(setStatus);
            toaster.toast({ title: "SteamKit", body: r.error ?? "Failed" });
            return;
          }
          for (const g of chosen) {
            SteamClient.Apps.RemoveShortcut(g.appid);
            await forget(g.appid);
          }
          setSelected({});
          refresh();
        }}
      />,
    );

  return (
    <div style={{ marginTop: 40, height: "calc(100% - 40px)", overflowY: "scroll", padding: "0 24px 80px" }}>
      <PanelSection title={`Added by SteamKit (${games.length})`}>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={games.length === 0 || !!status}
            onClick={() => setSelected(allOn ? {} : Object.fromEntries(games.map((g) => [g.appid, true])))}
          >
            {allOn ? "Select none" : "Select all"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={chosen.length === 0 || !!status} onClick={getArt}>
            {status || `Get artwork for ${chosen.length} selected`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={chosen.length !== 1 || !!status} onClick={() => findArt(chosen[0])}>
            Search artwork by name
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={chosen.length === 0 || !!status} onClick={remove}>
            {`Remove ${chosen.length} selected`}
          </ButtonItem>
        </PanelSectionRow>
        {missing.length > 0 && (
          <PanelSectionRow>
            <Field label="No artwork for:" description={missing.join(" · ")} />
          </PanelSectionRow>
        )}
        {games.length === 0 && (
          <PanelSectionRow>
            <Field label="Nothing added yet." />
          </PanelSectionRow>
        )}
        {games.map((g) => (
          <PanelSectionRow key={g.appid}>
            <ToggleField
              label={g.name}
              description={g.exe}
              checked={!!selected[g.appid]}
              onChange={(v) => setSelected({ ...selected, [g.appid]: v })}
            />
          </PanelSectionRow>
        ))}
      </PanelSection>
    </div>
  );
}

function RestorePage() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const refresh = () => savesInfo().then((i) => setBackups(i.backups));
  useEffect(() => {
    refresh();
  }, []);

  const restore = (b: Backup) =>
    showModal(
      <ConfirmModal
        strTitle="Restore this backup?"
        strDescription="Current saves are backed up first."
        strOKButtonText="Restore"
        onOK={async () => {
          const r = await restoreSaves(b.name);
          toaster.toast({ title: "SteamKit", body: r.ok ? "Saves restored" : `Failed: ${r.error}` });
          refresh();
        }}
      />,
    );

  return (
    <div style={{ marginTop: 40, height: "calc(100% - 40px)", overflowY: "scroll", padding: "0 24px 80px" }}>
      <PanelSection title={`Backups (${backups.length})`}>
        {backups.length === 0 && (
          <PanelSectionRow>
            <Field label="No backups yet." />
          </PanelSectionRow>
        )}
        {backups.map((b) => (
          <PanelSectionRow key={b.name}>
            <ButtonItem
              label={new Date(b.time * 1000).toLocaleString()}
              description={`${fmtSize(b.size)}${b.name.includes("before-restore") ? " · pre-restore" : ""}`}
              onClick={() => restore(b)}
            >
              Restore
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
    </div>
  );
}

function SavesSection({ settings, reload }: { settings: Settings | null; reload: () => void }) {
  const [info, setInfo] = useState<SavesInfo | null>(null);
  const [busy, setBusy] = useState("");
  const enabled = !!settings?.shared_saves;
  const refresh = () => savesInfo().then(setInfo);
  useEffect(() => {
    if (enabled) linkSaves().then(refresh); // pick up games launched since last time
    else refresh();
  }, [enabled]);

  const enable = (on: boolean) => {
    if (!on) {
      setSetting("shared_saves", false).then(reload);
      toaster.toast({ title: "SteamKit", body: "Shared saves off. Linked games stay linked." });
      return;
    }
    showModal(
      <ConfirmModal
        strTitle="Turn on shared saves?"
        strDescription="All games will save to one SteamKitSaves folder. Existing saves are moved, never overwritten."
        strOKButtonText="Turn on"
        onOK={async () => {
          await setSetting("shared_saves", true);
          reload();
          const r = await linkSaves();
          toaster.toast({ title: "SteamKit", body: summarizeLink(r) });
          refresh();
        }}
      />,
    );
  };

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } finally {
      setBusy("");
      refresh();
    }
  };

  const changeLocation = async () => {
    const res = await openFilePicker(FileSelectionType.FOLDER, settings?.last_dir ?? "/", false);
    showModal(
      <ConfirmModal
        strTitle="Move SteamKitSaves?"
        strDescription={`To ${res.realpath}. Close running games first.`}
        strOKButtonText="Move"
        onOK={() =>
          run("Moving…", async () => {
            const r = await setSavesParent(res.realpath);
            toaster.toast({ title: "SteamKit", body: r.ok ? `Saves now at ${r.root}` : `Failed: ${r.error}` });
          })
        }
      />,
    );
  };

  const linkedCount = info?.games.filter((g) => g.linked).length ?? 0;
  const latest = info?.backups[0];

  return (
    <PanelSection title="Saves">
      <PanelSectionRow>
        <ToggleField
          label="Shared saves"
          description={enabled ? `${linkedCount}/${info?.games.length ?? 0} linked · ${info?.root ?? ""}` : "Off"}
          checked={enabled}
          onChange={enable}
        />
      </PanelSectionRow>
      {enabled && (
        <>
          <PanelSectionRow>
            <ToggleField
              label="Include desktop Wine"
              description="~/.wine"
              checked={settings?.include_desktop_wine !== false}
              onChange={async (v) => {
                await setSetting("include_desktop_wine", v);
                reload();
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!!busy}
              onClick={() =>
                run("Linking…", async () => {
                  const r = await linkSaves();
                  toaster.toast({ title: "SteamKit", body: summarizeLink(r) });
                })
              }
            >
              {busy === "Linking…" ? busy : "Link saves now"}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!!busy}
              description={latest ? `Last: ${new Date(latest.time * 1000).toLocaleString()}` : undefined}
              onClick={() =>
                run("Backing up…", async () => {
                  const r = await backupSaves();
                  toaster.toast({ title: "SteamKit", body: r.ok ? `Backed up (${fmtSize(r.size ?? 0)})` : `Failed: ${r.error}` });
                })
              }
            >
              {busy === "Backing up…" ? busy : "Back up now"}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!!busy || !latest}
              onClick={() => {
                Navigation.CloseSideMenus();
                Navigation.Navigate(RESTORE_ROUTE);
              }}
            >
              Restore a backup…
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={!!busy} onClick={changeLocation}>
              {busy === "Moving…" ? busy : "Change save location…"}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

let syncTarget: { id: string; name: string } = { id: "", name: "" };

function SyncPage() {
  const [folders, setFolders] = useState<LanFolder[]>([]);
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [mode, setMode] = useState("newest");
  const [busy, setBusy] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    lanFolders(syncTarget.id).then((r) => {
      setFolders(r.folders);
      setTracked(new Set(r.tracked));
      setError(r.error);
      setLoaded(true);
    });
  }, []);

  const toggle = (entry: string, on: boolean) =>
    setTracked((t) => {
      const next = new Set(t);
      on ? next.add(entry) : next.delete(entry);
      return next;
    });

  const save = async () => {
    const r = await lanSetTracked([...tracked]);
    if (!r.ok) toaster.toast({ title: "SteamKit", body: `Failed: ${r.error}` });
    return r.ok;
  };

  const sync = async () => {
    if (!(await save())) return;
    setBusy("Syncing…");
    try {
      const r = await lanSync(syncTarget.id, mode);
      const body = r.ok
        ? `Pulled ${r.pulled}, pushed ${r.pushed}${r.conflicts?.length ? `, ${r.conflicts.length} conflict(s) left alone` : ""}`
        : `Failed: ${r.error}`;
      toaster.toast({ title: "SteamKit", body });
    } finally {
      setBusy("");
    }
  };

  const likely = folders.filter((f) => f.likely);
  const other = folders.filter((f) => !f.likely);
  const row = (f: LanFolder) => (
    <PanelSectionRow key={f.entry}>
      <ToggleField
        label={f.entry}
        description={`${f.where === "there" ? `Only on ${syncTarget.name}` : f.where === "here" ? "Only here" : f.where === "both" ? "On both" : "Not found"} · ${f.files} files · ${fmtSize(f.size)}`}
        checked={tracked.has(f.entry)}
        onChange={(v) => toggle(f.entry, v)}
      />
    </PanelSectionRow>
  );

  return (
    <div style={{ marginTop: 40, height: "calc(100% - 40px)", overflowY: "scroll", padding: "0 24px 80px" }}>
      <PanelSection title={`Sync with ${syncTarget.name}`}>
        {error && (
          <PanelSectionRow>
            <Field label={error} />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem
            label="Direction"
            rgOptions={[
              { data: "newest", label: "Newest file wins, both ways" },
              { data: "pull", label: `Get from ${syncTarget.name}` },
              { data: "push", label: `Send to ${syncTarget.name}` },
            ]}
            selectedOption={mode}
            onChange={(o) => setMode(o.data)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!!busy || tracked.size === 0} onClick={sync}>
            {busy || `Sync ${tracked.size} folder${tracked.size === 1 ? "" : "s"}`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!!busy} onClick={save}>
            Save selection only
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      {loaded && folders.length === 0 && (
        <PanelSection>
          <PanelSectionRow>
            <Field label="No save folders yet. Launch a game with shared saves on." />
          </PanelSectionRow>
        </PanelSection>
      )}
      {likely.length > 0 && <PanelSection title="Look like game saves">{likely.map(row)}</PanelSection>}
      {other.length > 0 && <PanelSection title="Other folders">{other.map(row)}</PanelSection>}
    </div>
  );
}

function LanSection({ settings }: { settings: Settings | null }) {
  const [info, setInfo] = useState<LanInfo | null>(null);
  const [found, setFound] = useState<LanPeer[]>([]);
  const [scanning, setScanning] = useState(false);
  const refresh = () => lanInfo().then(setInfo);
  useEffect(() => {
    refresh();
  }, [settings?.shared_saves]);

  const canUse = !!settings?.shared_saves;
  const enabled = !!info?.enabled;

  const toggle = async (on: boolean) => {
    const r = await setLan(on);
    if (!r.ok) toaster.toast({ title: "SteamKit", body: `Failed: ${r.error}` });
    refresh();
  };

  const find = async () => {
    setScanning(true);
    try {
      setFound(await lanScan());
      refresh();
    } finally {
      setScanning(false);
    }
  };

  const showCode = async () => {
    const r = await lanPairCode();
    if (!r.ok) return toaster.toast({ title: "SteamKit", body: r.error ?? "Failed" });
    showModal(
      <ConfirmModal
        strTitle="Pairing code"
        strDescription={`Enter ${r.code} on the other device within ${Math.round((r.seconds ?? 120) / 60)} minutes.`}
        strOKButtonText="Done"
        bAlertDialog
      />,
    );
  };

  const pair = (p: LanPeer) => {
    let code = "";
    showModal(
      <ConfirmModal
        strTitle={`Pair with ${p.name}`}
        strDescription="On that device choose “Show pairing code”, then enter the 6 digits."
        strOKButtonText="Pair"
        onOK={async () => {
          const r = await lanPair(p.host, p.port, code);
          toaster.toast({ title: "SteamKit", body: r.ok ? `Paired with ${r.name}` : `Failed: ${r.error}` });
          find();
        }}
      >
        <TextField label="Code" onChange={(e) => (code = e.target.value)} />
      </ConfirmModal>,
    );
  };

  const open = (p: { id: string; name: string }) => {
    syncTarget = { id: p.id, name: p.name };
    Navigation.CloseSideMenus();
    Navigation.Navigate(SYNC_ROUTE);
  };

  const rename = () => {
    let name = info?.name ?? "";
    showModal(
      <ConfirmModal
        strTitle="This device's name"
        onOK={async () => {
          await lanSetName(name);
          refresh();
        }}
      >
        <TextField label="Name" defaultValue={name} onChange={(e) => (name = e.target.value)} />
      </ConfirmModal>,
    );
  };

  const reachable = new Set(found.filter((p) => p.paired).map((p) => p.id));
  return (
    <PanelSection title="Sync with other devices">
      <PanelSectionRow>
        <ToggleField
          label="LAN sync"
          description={canUse ? (enabled ? "Visible to devices on this network" : "Off") : "Turn on shared saves first"}
          checked={enabled}
          disabled={!canUse}
          onChange={toggle}
        />
      </PanelSectionRow>
      {enabled && (
        <>
          <PanelSectionRow>
            <ButtonItem layout="below" description={info?.name} onClick={rename}>
              Device name
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={showCode}>
              Show pairing code
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={scanning} onClick={find}>
              {scanning ? "Looking…" : "Find devices"}
            </ButtonItem>
          </PanelSectionRow>
          {found
            .filter((p) => !p.paired)
            .map((p) => (
              <PanelSectionRow key={p.id}>
                <ButtonItem layout="below" description={p.host} onClick={() => pair(p)}>
                  Pair with {p.name}
                </ButtonItem>
              </PanelSectionRow>
            ))}
          {info?.peers.map((p) => (
            <PanelSectionRow key={p.id}>
              <ButtonItem
                layout="below"
                description={reachable.has(p.id) ? "Paired · online" : "Paired · press Find devices"}
                disabled={!reachable.has(p.id)}
                onClick={() => open(p)}
              >
                Sync with {p.name}
              </ButtonItem>
            </PanelSectionRow>
          ))}
          {info?.peers.map((p) => (
            <PanelSectionRow key={`un-${p.id}`}>
              <ButtonItem
                layout="below"
                onClick={() =>
                  showModal(
                    <ConfirmModal
                      strTitle={`Forget ${p.name}?`}
                      strDescription="You will need to pair again. Saves are not touched."
                      strOKButtonText="Forget"
                      onOK={async () => {
                        await lanUnpair(p.id);
                        refresh();
                      }}
                    />,
                  )
                }
              >
                Forget {p.name}
              </ButtonItem>
            </PanelSectionRow>
          ))}
        </>
      )}
    </PanelSection>
  );
}

function DrivesSection() {
  const [drives, setDrives] = useState<Drive[]>([]);
  const refresh = () => listDrives().then(setDrives);
  useEffect(() => {
    refresh();
  }, []);

  const setup = (d: Drive) => {
    let mp = d.suggested;
    const risky = ["ntfs", "exfat", "vfat"].includes(d.fstype);
    showModal(
      <ConfirmModal
        strTitle={`Mount ${d.label || d.name} at boot`}
        strOKButtonText="Mount at boot"
        onOK={async () => {
          const r = await mountDrive(d.uuid, mp);
          toaster.toast({ title: "SteamKit", body: r.ok ? `Mounted at ${mp}` : `Failed: ${r.error}` });
          refresh();
        }}
      >
        <TextField label="Mount point" defaultValue={mp} onChange={(e) => (mp = e.target.value)} />
        {risky && <div style={{ marginTop: 8 }}>⚠ {d.fstype}: ext4/btrfs is more reliable for Proton.</div>}
      </ConfirmModal>,
    );
  };

  const unconfigured = drives.filter((d) => !d.configured);
  return (
    <PanelSection title="Drives">
      {unconfigured.length === 0 && (
        <PanelSectionRow>
          <Field label="All drives mount at boot" />
        </PanelSectionRow>
      )}
      {unconfigured.map((d) => (
        <PanelSectionRow key={d.uuid}>
          <ButtonItem
            layout="below"
            description={`${d.size} ${d.fstype}${d.mountpoint ? ` · ${d.mountpoint}` : ""}`}
            onClick={() => setup(d)}
          >
            Auto-mount {d.label || d.name}
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}

function Content() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [scanning, setScanning] = useState("");
  const [locations, setLocations] = useState<Location[]>([]);
  useEffect(() => {
    getSettings().then(setSettings);
    listLocations().then(setLocations);
    reportFinishedJob();
  }, []);

  const browse = async () => {
    const res = await openFilePicker(FileSelectionType.FOLDER, settings?.last_dir ?? "/", false);
    return scanDir(res.realpath);
  };

  const scanDir = async (dir: string) => {
    setScanning(dir);
    try {
      pending = { dir, games: await scan(dir) };
      if (pending.games.length === 0) {
        toaster.toast({ title: "SteamKit", body: "No games found." });
        return;
      }
      Navigation.CloseSideMenus();
      Navigation.Navigate(ADD_ROUTE);
    } finally {
      setScanning("");
    }
  };

  const restartSteam = () =>
    showModal(
      <ConfirmModal
        strTitle="Restart Steam?"
        strDescription="Running games will close."
        strOKButtonText="Restart"
        onOK={() => SteamClient.User.StartRestart(false)}
      />,
    );

  const editKey = () => {
    let key = settings?.sgdb_key ?? "";
    showModal(
      <ConfirmModal
        strTitle="SteamGridDB API key"
        strDescription="steamgriddb.com → Preferences → API"
        onOK={async () => {
          await setSetting("sgdb_key", key.trim());
          setSettings(await getSettings());
        }}
      >
        <TextField label="API key" bIsPassword defaultValue={key} onChange={(e) => (key = e.target.value)} />
      </ConfirmModal>,
    );
  };

  return (
    <>
      <PanelSection title="Scan for games">
        {locations.map((l) => (
          <PanelSectionRow key={l.path}>
            <ButtonItem layout="below" description={l.path} onClick={() => scanDir(l.path)} disabled={!!scanning}>
              {scanning === l.path ? "Scanning…" : l.label}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={browse} disabled={!!scanning} >
            {scanning && !locations.some((l) => l.path === scanning) ? "Scanning…" : "Browse for folder…"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Games">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => {
              Navigation.CloseSideMenus();
              Navigation.Navigate(MANAGE_ROUTE);
            }}
          >
            Manage added games
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={restartSteam}>
            Restart Steam
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <SavesSection settings={settings} reload={() => getSettings().then(setSettings)} />
      <LanSection settings={settings} />
      <DrivesSection />
      <PanelSection title="Settings">
        {!settings?.gaming_mode && (
        <PanelSectionRow>
          <ToggleField
            label="Restart Steam to apply"
            description="Desktop mode only"
            checked={settings?.mode !== "live"}
            onChange={async (v) => {
              await setSetting("mode", v ? "restart" : "live");
              setSettings(await getSettings());
            }}
          />
        </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={editKey} description={settings?.sgdb_key ? "Set" : "Needed for artwork"}>
            SteamGridDB key
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  routerHook.addRoute(ADD_ROUTE, AddGamesPage);
  routerHook.addRoute(MANAGE_ROUTE, ManagePage);
  routerHook.addRoute(RESTORE_ROUTE, RestorePage);
  routerHook.addRoute(SYNC_ROUTE, SyncPage);
  return {
    name: "SteamKit",
    titleView: <div className={staticClasses.Title}>SteamKit</div>,
    content: <Content />,
    icon: <FaGamepad />,
    onDismount() {
      routerHook.removeRoute(ADD_ROUTE);
      routerHook.removeRoute(MANAGE_ROUTE);
      routerHook.removeRoute(RESTORE_ROUTE);
      routerHook.removeRoute(SYNC_ROUTE);
    },
  };
});
