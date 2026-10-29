import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, ArrowUpRight, Check, ChevronDown, ChevronRight, CircleHelp, Command,
  FileImage, FileText, FolderClosed, HardDrive, Image as ImageIcon, LayoutGrid,
  ListFilter, LoaderCircle, Menu, MoreHorizontal, Plus, Settings2, ShieldCheck,
  Sparkles, SlidersHorizontal, Upload, Video, X, ScanSearch, Database, CircleCheck,
  AlertTriangle, FolderOpen, RotateCcw, CheckCircle2
} from "lucide-react";

type Asset = {
  id: number;
  title: string;
  date: string;
  kind: "Photo" | "Video" | "Document";
  collection: string;
  tags: string[];
  src: string;
  width?: string;
  filename?: string; sizeBytes?: number; widthPx?: number; heightPx?: number;
  mediaType?: string; capturedAt?: string | null; ingestedAt?: string | null;
  semanticMatches?: { label: string; score: number; model?: string }[];
  employeeNames?: string[];
  status?: string;
};

const demoAssets: Asset[] = [
  { id: 1, title: "Monsoon on the city edge", date: "18 Oct 2024", kind: "Photo", collection: "Bengaluru fieldwork", tags: ["urban ecology", "rainfall", "street"], src: "https://images.unsplash.com/photo-1519608487953-e999c86e7455?auto=format&fit=crop&w=900&q=85" },
  { id: 2, title: "A walk through the archive", date: "04 Feb 2023", kind: "Photo", collection: "Campus life", tags: ["people", "campus", "research"], src: "https://images.unsplash.com/photo-1497366754035-f200968a6e72?auto=format&fit=crop&w=900&q=85" },
  { id: 3, title: "Water, settlement, and memory", date: "21 Aug 2022", kind: "Photo", collection: "Urban systems", tags: ["water", "settlement", "landscape"], src: "https://images.unsplash.com/photo-1500375592092-40eb2168fd21?auto=format&fit=crop&w=900&q=85" },
  { id: 4, title: "Field notes — north corridor", date: "12 Mar 2024", kind: "Document", collection: "Field notes", tags: ["fieldwork", "notes", "planning"], src: "https://images.unsplash.com/photo-1455390582262-044cdead277a?auto=format&fit=crop&w=900&q=85" },
  { id: 5, title: "A changing urban canopy", date: "09 Jun 2023", kind: "Photo", collection: "Ecology & environment", tags: ["trees", "ecology", "urban"], src: "https://images.unsplash.com/photo-1448375240586-882707db888b?auto=format&fit=crop&w=900&q=85" },
  { id: 6, title: "Infrastructure in transition", date: "27 Nov 2021", kind: "Photo", collection: "Built environment", tags: ["infrastructure", "construction", "city"], src: "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=900&q=85" },
  { id: 7, title: "The research team, on location", date: "15 Jan 2024", kind: "Video", collection: "Fieldwork stories", tags: ["people", "fieldwork", "interview"], src: "https://images.unsplash.com/photo-1521737711867-e3b97375f902?auto=format&fit=crop&w=900&q=85" },
  { id: 8, title: "Edges of the city", date: "06 Sep 2020", kind: "Photo", collection: "Bengaluru fieldwork", tags: ["city", "housing", "landscape"], src: "https://images.unsplash.com/photo-1518005020951-eccb494ad742?auto=format&fit=crop&w=900&q=85" }
];

type Source = "files" | "folder" | "drive";
type Page = "Archive" | "Ingest" | "Review queue" | "Collections" | "Datasets";

const navItems: { label: Page; icon: typeof Archive; count?: string }[] = [
  { label: "Archive", icon: LayoutGrid },
  { label: "Ingest", icon: Upload },
  { label: "Review queue", icon: ScanSearch },
  { label: "Collections", icon: FolderClosed },
  { label: "Datasets", icon: Database }
];

function App() {
  const [page, setPage] = useState<Page>("Archive");
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState("All media");
  const [selected, setSelected] = useState<Asset | null>(null);
  const [source, setSource] = useState<Source>("folder");
  const [recursive, setRecursive] = useState(true);
  const [metadata, setMetadata] = useState(true);
  const [thumbnails, setThumbnails] = useState(true);
  const [tags, setTags] = useState(true);
  const [scanState, setScanState] = useState<"idle" | "preview" | "running" | "complete">("idle");
  const [sourceName, setSourceName] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [liveAssets, setLiveAssets] = useState<Asset[] | null>(null);
  const [apiState, setApiState] = useState<"loading" | "connected" | "offline">("loading");
  const [reviewCount, setReviewCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch("http://127.0.0.1:8000/api/v1/assets?limit=100")
      .then((response) => { if (!response.ok) throw new Error("API unavailable"); return response.json(); })
      .then((rows: any[]) => {
        if (cancelled) return;
        setLiveAssets(rows.map((row) => ({
          id: row.id, title: row.name, date: row.taken_at ? new Date(row.taken_at).toLocaleDateString() : "Capture date unknown",
          kind: row.media_type?.startsWith("image/") ? "Photo" : row.media_type?.startsWith("video/") ? "Video" : "Document",
          collection: row.path, tags: (row.tags ?? []).map((tag: any) => tag.label), src: row.thumbnail_url ? "http://127.0.0.1:8000" + row.thumbnail_url : "",
          filename: row.name, sizeBytes: row.size_bytes, widthPx: row.width, heightPx: row.height, mediaType: row.media_type,
          capturedAt: row.taken_at ?? null, ingestedAt: row.ingested_at ?? null,
          semanticMatches: (row.semantic_matches ?? []).map((match: any) => ({ label: match.label, score: match.score, model: match.model })),
          employeeNames: (row.verified_employees ?? []).map((person: any) => person.display_name),
          status: row.status ?? "indexed",
        })));
        setReviewCount(rows.filter((row) => row.status === "review_requested").length);
        setApiState("connected");
      })
      .catch(() => { if (!cancelled) setApiState("offline"); });
    return () => { cancelled = true; };
  }, []);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const filteredAssets = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (liveAssets ?? demoAssets).filter((asset) => {
      if (page === "Review queue" && asset.status !== "review_requested") return false;
      const matchesQuery = !normalized || [asset.title, asset.collection, asset.kind, ...asset.tags].join(" ").toLowerCase().includes(normalized);
      const matchesType = activeFilter === "All media" || (activeFilter === "Images" && asset.kind === "Photo") || (activeFilter === "Videos" && asset.kind === "Video") || (activeFilter === "Documents" && asset.kind === "Document");
      return matchesQuery && matchesType;
    });
  }, [query, activeFilter, liveAssets, page]);

  const showNotice = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 3600);
  };

  const handleSourcePick = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const first = files[0];
    setSourceName(files.length === 1 ? first.name : `${first.name} + ${files.length - 1} more`);
    setScanState("idle");
    showNotice("Selection loaded in preview mode. No files have been uploaded or processed.");
  };

  const runPreview = () => {
    if (source === "drive") {
      showNotice("Drive scan requires a configured server mount or trusted local agent.");
      return;
    }
    if (!sourceName) {
      (source === "folder" ? folderInput.current : fileInput.current)?.click();
      return;
    }
    setScanState("preview");
  };

  const startIngestion = () => {
    setScanState("running");
    showNotice("Demo only: connect the ingestion API to process real media.");
    window.setTimeout(() => setScanState("complete"), 1200);
  };

  const downloadAsset = (asset: Asset) => {
    if (!liveAssets) { showNotice("Download is available for indexed archive records, not illustrative sample cards."); return; }
    window.open(`http://127.0.0.1:8000/api/v1/assets/${asset.id}/download`, "_blank", "noopener,noreferrer");
  };

  const addAssetTag = async (asset: Asset) => {
    if (!liveAssets) { showNotice("Add tag is available for indexed archive records only."); return; }
    const label = window.prompt("Enter an IIHS keyword or tag:");
    if (!label?.trim()) return;
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/v1/assets/${asset.id}/tags`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label.trim(), source: "manual" })
      });
      if (!response.ok) throw new Error("Tag could not be saved");
      const saved = await response.json();
      const updated = { ...asset, tags: [...asset.tags, saved.label] };
      setLiveAssets((items) => items?.map((item) => item.id === asset.id ? updated : item) ?? null);
      setSelected(updated);
      showNotice("Keyword saved to the archive record.");
    } catch {
      showNotice("Could not save the tag. Check that the local API is running.");
    }
  };

  const sendAssetToReview = async (asset: Asset) => {
    if (!liveAssets) { showNotice("Review submission is available for indexed archive records only."); return; }
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/v1/assets/${asset.id}/review`, { method: "POST" });
      if (!response.ok) throw new Error("Review submission failed");
      const result = await response.json();
      setLiveAssets((items) => items?.map((item) => item.id === asset.id ? { ...item, status: "review_requested" } : item) ?? null);
      setReviewCount((count) => count + (asset.status === "review_requested" ? 0 : 1));
      setSelected({ ...asset, status: "review_requested" });
      showNotice(result.message ?? "Asset sent to review.");
    } catch {
      showNotice("Could not send this asset to review. Check that the local API is running.");
    }
  };

  const pickSource = (next: Source) => {
    setSource(next);
    setSourceName("");
    setScanState("idle");
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark"><span></span><span></span><span></span><span></span></div>
          <div className="brand-copy"><strong>IIHS<span> / </span>ARCHIVE</strong><small>INSTITUTE FOR HUMAN SETTLEMENTS</small></div>
          <button className="icon-button mobile-close" onClick={() => setMobileNav(false)} aria-label="Close menu"><X size={17}/></button>
        </div>
        <div className="workspace-pill"><div className="workspace-avatar">I</div><div><strong>Research archive</strong><small>Local workspace</small></div><ChevronDown size={14}/></div>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="main-nav">
          {navItems.map(({ label, icon: Icon, count }) => (
            <button key={label} className={`nav-item ${page === label ? "active" : ""}`} onClick={() => { setPage(label); setMobileNav(false); }}>
              <Icon size={17} strokeWidth={1.8}/><span>{label}</span>{label === "Review queue" ? <span className="nav-count">{reviewCount}</span> : count && <span className="nav-count">{count}</span>}
            </button>
          ))}
        </nav>
        <div className="nav-caption collection-caption">YOUR COLLECTIONS <button aria-label="Add collection" onClick={() => showNotice("Collection creation will be connected to the archive API.")}><Plus size={13}/></button></div>
        <div className="collection-link"><span className="collection-dot rust"></span><span>Bengaluru fieldwork</span><small>286</small></div>
        <div className="collection-link"><span className="collection-dot moss"></span><span>Ecology & environment</span><small>142</small></div>
        <div className="collection-link"><span className="collection-dot blue"></span><span>Built environment</span><small>98</small></div>
        <div className="collection-link"><span className="collection-dot gold"></span><span>Campus life</span><small>73</small></div>
        <div className="sidebar-spacer"></div>
        <div className="storage-card">
          <div className="storage-head"><span><HardDrive size={14}/> Local storage</span><span className="live-dot"></span></div>
          <div className="storage-amount">1.8 <small>TB <span>of 4 TB</span></small></div>
          <div className="storage-bar"><span></span></div>
          <div className="storage-foot"><span>45% used</span><span>Demo data</span></div>
        </div>
        <button className="user-row" onClick={() => showNotice("Account settings will be available with authentication.")}>
          <div className="user-avatar">AR</div><div className="user-details"><strong>Archive researcher</strong><small>Workspace member</small></div><MoreHorizontal size={17}/>
        </button>
      </aside>

      {mobileNav && <button className="mobile-scrim" onClick={() => setMobileNav(false)} aria-label="Close navigation"/>}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu size={20}/></button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14}/><strong>{page}</strong></div>
          <div className="topbar-right">
            <span className="local-badge"><span className="live-dot"></span> LOCAL-FIRST</span>
            <button className="icon-button" aria-label="Help" onClick={() => showNotice("Use Search for meaning-based discovery; select Ingest to add a source.")}><CircleHelp size={18}/></button>
            <button className="icon-button" aria-label="Settings" onClick={() => showNotice("Settings will be connected to the backend.")}><Settings2 size={18}/></button>
            <div className="top-avatar">AR</div>
          </div>
        </header>

        {(page === "Archive" || page === "Review queue") && <section className="page-content">
          <div className="eyebrow"><span className="eyebrow-line"></span> {page === "Review queue" ? "HUMAN REVIEW / OPTIONAL" : "THE INSTITUTIONAL MEMORY"}</div>
          <div className="hero-row">
            <div><h1>{page === "Review queue" ? <>A second pair<br/><em>of eyes.</em></> : <>Every place has<br/><em>a story to find.</em></>}</h1><p className="hero-subtitle">{page === "Review queue" ? "Only assets explicitly submitted for review appear here. Ingestion does not require review." : "A living archive of places, people, and the systems that shape how we live."}</p></div>
            {page === "Archive" && <button className="primary-button hero-ingest" onClick={() => setPage("Ingest")}><Plus size={16}/> Add to archive <ArrowUpRight size={15}/></button>}
          </div>
          <div className="search-panel">
            <div className="search-icon"><Sparkles size={18}/></div>
            <div className="search-field"><label htmlFor="archive-search">SEARCH BY MEMORY, NOT JUST METADATA</label><input id="archive-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Try “a crowded street after the rain” or search a keyword…" /></div>
            <kbd><Command size={12}/> K</kbd>
            <button className="search-action" onClick={() => showNotice("Semantic search is shown as a design prototype; EmbeddingGemma 2 must be connected in the local backend.")}><ArrowRight size={18}/></button>
          </div>
          <div className="search-hint"><Sparkles size={12}/> Local archive API: {apiState === "connected" ? "connected" : apiState === "loading" ? "connecting…" : "offline"} <span>·</span> <span className="muted">{liveAssets ? "Showing indexed archive records" : "Showing clearly labelled illustrative sample data"}</span></div>

          <div className="stats-row">
            <div className="stat-item"><span className="stat-label">ARCHIVE ITEMS</span><div className="stat-value">12,486 <span className="stat-delta">+248 this month</span></div></div>
            <div className="stat-item"><span className="stat-label">COLLECTIONS</span><div className="stat-value">36 <span className="stat-muted">curated groups</span></div></div>
            <div className="stat-item"><span className="stat-label">NEEDS REVIEW</span><div className="stat-value">24 <span className="stat-review">Awaiting a human</span></div></div>
            <div className="stat-item"><span className="stat-label">INDEX HEALTH</span><div className="stat-value"><span className="health-dot"></span> Ready <span className="stat-muted">Local index</span></div></div>
          </div>

          <div className="section-heading">
            <div><div className="eyebrow small-eyebrow">{page === "Review queue" ? "SUBMITTED BY A HUMAN" : "EXPLORE THE ARCHIVE"}</div><h2>{page === "Review queue" ? "Assets awaiting review" : "Recently collected"} <span>↘</span></h2></div>
            {page === "Archive" && <button className="text-button" onClick={() => showNotice("The archive list is limited to the current API page.")}>View all media <ArrowRight size={15}/></button>}
          </div>
          <div className="filter-bar">
            <div className="filter-tabs">{["All media", "Images", "Videos", "Documents"].map((filter) => <button key={filter} onClick={() => setActiveFilter(filter)} className={activeFilter === filter ? "selected" : ""}>{filter}{filter === "All media" && <span className="filter-total">8</span>}</button>)}</div>
            <div className="filter-actions"><span className="results-count">{filteredAssets.length} {liveAssets ? "archive items" : "sample items"}</span><button className="filter-button" onClick={() => showNotice("Collection and date filters will be wired to the archive API.")}><SlidersHorizontal size={15}/> Filters</button><div className="view-toggle"><button className={view === "grid" ? "selected" : ""} onClick={() => setView("grid")} aria-label="Grid view"><LayoutGrid size={15}/></button><button className={view === "list" ? "selected" : ""} onClick={() => setView("list")} aria-label="List view"><ListFilter size={15}/></button></div></div>
          </div>
          <div className={`asset-grid ${view === "list" ? "asset-list" : ""}`}>
            {filteredAssets.map((asset, index) => <button key={asset.id} className="asset-card" onClick={() => setSelected(asset)}>
              <div className="asset-thumb"><img src={asset.src} alt="" loading="lazy"/><span className="asset-kind">{asset.kind === "Photo" ? <ImageIcon size={12}/> : asset.kind === "Video" ? <Video size={12}/> : <FileText size={12}/>} {asset.kind.toUpperCase()}</span><span className="asset-index">0{index + 1}</span><span className="asset-open"><ArrowUpRight size={17}/></span></div>
              <div className="asset-info"><div className="asset-meta"><span>{asset.collection}</span><span>{asset.date}</span></div><h3>{asset.title}</h3><div className="tag-row">{asset.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>{asset.sizeBytes !== undefined && <div className="asset-card-facts"><span>{asset.sizeBytes < 1048576 ? `${Math.round(asset.sizeBytes / 1024)} KB` : `${(asset.sizeBytes / 1048576).toFixed(1)} MB`}</span>{asset.widthPx && asset.heightPx && <span>{asset.widthPx} × {asset.heightPx}</span>}</div>}</div>
            </button>)}
          </div>
          <div className="archive-footer"><span>{page === "Review queue" ? `${filteredAssets.length} assets requested for review` : liveAssets ? "Showing indexed archive records" : "Showing illustrative sample data"}</span><span><span className="footer-dot"></span> {liveAssets ? "Local API records" : "Sample files are illustrative"}</span></div>
        </section>}

        {page === "Ingest" && <section className="page-content ingest-page">
          <div className="eyebrow"><span className="eyebrow-line"></span> ARCHIVE WORKFLOW / 01</div>
          <div className="ingest-title-row"><div><h1>Bring something<br/><em>into the archive.</em></h1><p className="hero-subtitle">Select a source, set the rules, and review everything before processing begins.</p></div><span className="workflow-pill"><ShieldCheck size={15}/> Safe by design</span></div>
          <div className="stepper"><div className="step active"><span>01</span><div><strong>Choose source</strong><small>Where the media lives</small></div></div><div className="step-line"></div><div className={`step ${scanState !== "idle" ? "active" : ""}`}><span>02</span><div><strong>Review scan</strong><small>Inspect before processing</small></div></div><div className="step-line"></div><div className={`step ${scanState === "running" || scanState === "complete" ? "active" : ""}`}><span>03</span><div><strong>Ingest</strong><small>Process and index media</small></div></div></div>
          <div className="ingest-layout">
            <div className="ingest-main-card">
              <div className="card-head"><div><span className="card-kicker">STEP 01 / SOURCE</span><h2>Where should we look?</h2><p>Choose a source type. Nothing is scanned until you request a preview.</p></div><span className="card-icon"><FolderOpen size={19}/></span></div>
              <div className="source-options">
                <button className={`source-option ${source === "files" ? "chosen" : ""}`} onClick={() => pickSource("files")}><span className="source-icon"><FileImage size={20}/></span><strong>Specific files</strong><small>Select images, videos, or documents</small><span className="radio-circle">{source === "files" && <span/>}</span></button>
                <button className={`source-option ${source === "folder" ? "chosen" : ""}`} onClick={() => pickSource("folder")}><span className="source-icon"><FolderClosed size={20}/></span><strong>A folder</strong><small>Include subfolders if needed</small><span className="radio-circle">{source === "folder" && <span/>}</span></button>
                <button className={`source-option ${source === "drive" ? "chosen" : ""}`} onClick={() => pickSource("drive")}><span className="source-icon"><HardDrive size={20}/></span><strong>Server drive</strong><small>Approved archive mounts</small><span className="radio-circle">{source === "drive" && <span/>}</span></button>
              </div>
              <input ref={fileInput} type="file" multiple hidden accept="image/*,video/*,.pdf,.doc,.docx,.txt,.csv,.xlsx,.pptx,.mp3,.wav" onChange={(e) => handleSourcePick(e.target.files)}/>
              <input ref={folderInput} type="file" hidden multiple {...({webkitdirectory:""} as React.InputHTMLAttributes<HTMLInputElement>)} onChange={(e) => handleSourcePick(e.target.files)}/>
              {source === "drive" ? <div className="server-note"><ShieldCheck size={17}/><div><strong>Server storage is permission-controlled</strong><p>Select an administrator-approved mount in the connected backend. Arbitrary paths and full-disk scans are never accepted from the browser.</p></div></div> :
                <button className="dropzone" onClick={() => (source === "folder" ? folderInput.current : fileInput.current)?.click()}><span className="drop-icon"><Upload size={21}/></span><strong>{sourceName || (source === "folder" ? "Choose a folder to scan" : "Choose files to add")}</strong><small>{sourceName ? "Selection staged locally in this browser" : source === "folder" ? "Folder picker · Recursive scanning can be enabled below" : "Select multiple media files at once"}</small><span className="browse-button">Browse {source === "folder" ? "folder" : "files"} <ArrowRight size={14}/></span></button>}
              <div className="options-block"><div className="options-title"><div><h3>Processing preferences</h3><p>Use these defaults or adjust for this batch.</p></div><button className="quiet-icon" onClick={() => {setRecursive(true);setMetadata(true);setThumbnails(true);setTags(true);}} aria-label="Reset options"><RotateCcw size={15}/></button></div>
                <Toggle title="Scan subfolders" description="Include nested directories" checked={recursive} onChange={setRecursive}/>
                <Toggle title="Extract metadata" description="Capture timestamps, dimensions, EXIF, and file details" checked={metadata} onChange={setMetadata}/>
                <Toggle title="Generate thumbnails" description="Create browse-friendly previews for supported media" checked={thumbnails} onChange={setThumbnails}/>
                <Toggle title="Suggest AI tags" description="Run local models after media validation" checked={tags} onChange={setTags} badge="LOCAL AI"/>
              </div>
              <div className="card-bottom"><div className="secure-note"><ShieldCheck size={15}/><span>Originals remain unchanged · Local-first processing</span></div><button className="primary-button" onClick={runPreview}>Review scan <ArrowRight size={16}/></button></div>
            </div>
            <aside className="ingest-side">
              <div className="side-card"><span className="card-kicker">BEFORE YOU START</span><h3>A little context goes a long way.</h3><div className="checklist-item"><Check size={15}/><span>Only scan approved locations</span></div><div className="checklist-item"><Check size={15}/><span>Review unsupported formats</span></div><div className="checklist-item"><Check size={15}/><span>Confirm duplicates before action</span></div><div className="checklist-item"><Check size={15}/><span>Verify disk space for derivatives</span></div></div>
              <div className="side-card model-card"><div className="model-heading"><span className="model-spark"><Sparkles size={16}/></span><span className="card-kicker">LOCAL EMBEDDINGS</span></div><h3>Search by meaning, not filenames.</h3><p>EmbeddingGemma 2 can power semantic retrieval after its local model worker is configured.</p><div className="model-state"><span className="status-dot"></span> Model integration <strong>Pending</strong></div></div>
              <div className="privacy-note"><ShieldCheck size={15}/><span>Selected files are not uploaded by this prototype. Production processing will run through your approved local server.</span></div>
            </aside>
          </div>
          {scanState === "preview" && <div className="preview-panel"><div className="preview-heading"><div><span className="card-kicker">STEP 02 / DRY RUN</span><h2>Review before ingesting</h2><p>This preview is illustrative until connected to the scanning API.</p></div><span className="preview-ready"><CircleCheck size={15}/> Preview ready</span></div><div className="preview-stats"><div><span>DISCOVERED</span><strong>{sourceName ? "Selected" : "—"}</strong></div><div><span>SUPPORTED</span><strong>Pending scan</strong></div><div><span>DUPLICATES</span><strong>Not checked</strong></div><div><span>ESTIMATED SIZE</span><strong>Not available</strong></div></div><div className="preview-warning"><AlertTriangle size={16}/><span>No real filesystem scan has run. Connect the backend before enabling production ingestion.</span></div><div className="preview-actions"><button className="secondary-button" onClick={() => setScanState("idle")}>Back to settings</button><button className="primary-button" onClick={startIngestion}>Start demo flow <ArrowRight size={15}/></button></div></div>}
          {(scanState === "running" || scanState === "complete") && <div className="preview-panel"><div className="preview-heading"><div><span className="card-kicker">STEP 03 / PROCESS</span><h2>{scanState === "complete" ? "Demo flow complete" : "Preparing demo flow…"}</h2><p>{scanState === "complete" ? "No files were actually ingested. The production job API is not connected yet." : "This is a short UI-only state demonstration."}</p></div>{scanState === "complete" ? <CheckCircle2 size={24}/> : <LoaderCircle className="spin" size={24}/>}</div></div>}
        </section>}

        {page !== "Archive" && page !== "Ingest" && <section className="page-content secondary-page"><div className="eyebrow"><span className="eyebrow-line"></span> WORKSPACE</div><h1>{page === "Review queue" ? <>A second pair<br/><em>of eyes.</em></> : page === "Collections" ? <>Knowledge, in<br/><em>good company.</em></> : <>From image to<br/><em>insight.</em></>}</h1><p className="hero-subtitle">{page === "Review queue" ? "AI suggestions are most useful when people can verify them." : page === "Collections" ? "Gather related media into purposeful, research-led collections." : "Curate verified images into repeatable training datasets."}</p><div className="empty-feature-card"><div className="empty-feature-icon">{page === "Review queue" ? <ScanSearch/> : page === "Collections" ? <FolderClosed/> : <Database/>}</div><div><h2>{page} workspace</h2><p>This workspace is included in the product direction. Connect the backend endpoints to replace this starter state with live records.</p><button className="secondary-button" onClick={() => setPage("Ingest")}>Open ingestion panel <ArrowRight size={15}/></button></div></div></section>}

        <footer className="main-footer"><span>IIHS MEDIA ARCHIVE <span className="footer-sep">/</span> FIELD NOTES FOR THE FUTURE</span><span><span className="footer-dot"></span> Prototype · Sample data only</span></footer>
      </main>
      {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><div className="asset-modal" onClick={(e) => e.stopPropagation()}><button className="modal-close" onClick={() => setSelected(null)} aria-label="Close detail"><X size={18}/></button><img src={selected.src} alt=""/><div className="modal-content"><span className="card-kicker">{selected.kind} / {liveAssets ? "ARCHIVE RECORD" : "ILLUSTRATIVE SAMPLE"}</span><h2>{selected.filename ?? selected.title}</h2><p>{selected.collection}</p><div className="asset-detail-grid"><div><small>CAPTURE DATE</small><strong>{selected.capturedAt ? new Date(selected.capturedAt).toLocaleString() : "Not recorded"}</strong></div><div><small>INGESTED</small><strong>{selected.ingestedAt ? new Date(selected.ingestedAt).toLocaleString() : "Not available"}</strong></div><div><small>FILE TYPE</small><strong>{selected.mediaType ?? selected.kind}</strong></div><div><small>FILE SIZE</small><strong>{selected.sizeBytes === undefined ? "Not available" : `${(selected.sizeBytes / 1048576).toFixed(2)} MB`}</strong></div><div><small>DIMENSIONS</small><strong>{selected.widthPx && selected.heightPx ? `${selected.widthPx} × ${selected.heightPx}` : "Not available"}</strong></div><div><small>PATH</small><strong>{selected.collection}</strong></div></div><section className="asset-detail-section"><h3>IIHS keywords</h3><div className="tag-row modal-tags">{selected.tags.length ? selected.tags.map((tag) => <span key={tag}>{tag}</span>) : <span className="detail-empty">No keywords assigned</span>}</div></section><section className="asset-detail-section"><h3>EmbeddingGemma 2 semantic matches</h3>{selected.semanticMatches?.length ? <div className="semantic-match-list">{selected.semanticMatches.map((match) => <div key={match.label}><span>{match.label}</span><strong>{(match.score * 100).toFixed(1)}%</strong><small>{match.model ?? "Model recorded by backend"}</small></div>)}</div> : <p className="detail-empty">No EmbeddingGemma 2 matches stored for this asset.</p>}</section><section className="asset-detail-section"><h3>Verified employee associations</h3>{selected.employeeNames?.length ? selected.employeeNames.map((name) => <span className="employee-chip" key={name}><ShieldCheck size={13}/>{name}</span>) : <p className="detail-empty">No verified employee association recorded.</p>}</section><div className="modal-meta"><span><ShieldCheck size={15}/> Record source <strong>{liveAssets ? "Local API" : "Sample only"}</strong></span></div><div className="asset-action-row"><button className="secondary-button" onClick={() => downloadAsset(selected)}><ArrowUpRight size={15}/> Download original</button><button className="secondary-button" onClick={() => addAssetTag(selected)}><Plus size={15}/> Add a tag</button><button className="primary-button" onClick={() => sendAssetToReview(selected)}><ScanSearch size={15}/> Send to review</button></div></div></div></div>}
      {notice && <div role="status" className="toast"><span className="toast-mark"><Check size={14}/></span>{notice}<button onClick={() => setNotice("")} aria-label="Dismiss notification"><X size={14}/></button></div>}
    </div>
  );
}

function Toggle({ title, description, checked, onChange, badge }: { title: string; description: string; checked: boolean; onChange: (value: boolean) => void; badge?: string }) {
  return <label className="toggle-row"><span className="toggle-copy"><strong>{title} {badge && <small>{badge}</small>}</strong><span>{description}</span></span><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}/><span className="toggle-track"><span/></span></label>;
}

export default App;
