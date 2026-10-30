import React, { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertTriangle, ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronDown, ChevronRight,
  CircleCheck, CircleHelp, Command, Database, FileImage, FolderClosed, FolderOpen,
  HardDrive, Image as ImageIcon, LayoutGrid, ListFilter, LoaderCircle, LogOut, Menu,
  Plus, RotateCcw, ScanSearch, Settings2, ShieldCheck, SlidersHorizontal, Sparkles,
  Upload, Video, X
} from './icons';
import './style.css';

type Role = 'admin' | 'archivist' | 'researcher' | 'operator';
type User = { id: string; username: string; role: Role; mode: 'demo' | 'restricted' };
type Asset = {
  id: string;
  original_filename: string;
  media_type: 'image' | 'video';
  detected_mime: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  has_thumbnail: boolean;
  created_at: string;
  semantic_score?: number;
  matched_frame_id?: string | null;
  matched_timestamp_ms?: number | null;
  matched_frame_score?: number | null;
  annotations: { id: string; label: string; source: string; status: string; confidence: number | null }[];
};
type Page<T> = { items: T[]; next_cursor: string | null };
type Job = {
  id: string;
  kind: string;
  state: string;
  source_id: string;
  progress: { done: number; total: number };
  summary: Record<string, number | boolean>;
  error: string | null;
  created_at: string;
  preview_files?: { path: string; type: string; size_bytes: number; state: string; message: string | null }[];
};
type Tag = { id: string; canonical_name: string; display_name: string; description: string | null; active: boolean };
type InsightItem = { label: string; score: number; category?: string; notes?: string };
type InsightResult = { status: 'ready' | 'unavailable' | 'not_indexed'; items: InsightItem[] };
type ReviewState = { status: 'pending' | 'reviewed' | 'requested'; pending_suggestions: number; requested_by: string; requested_at: string };
type AssetInsights = {
  source: {
    original_filename: string;
    source_name: string;
    source_kind: string;
    source_path: string | null;
    imported_by: string | null;
    imported_at: string;
    ingestion_job_id: string | null;
    sha256: string;
    mime: string;
    size_bytes: number;
    width: number | null;
    height: number | null;
    duration_ms: number | null;
  };
  camera: { fields: { label: string; value: string }[]; gps_present: boolean };
  objects: InsightResult;
  employees: { status: string; items: InsightItem[]; message: string };
  keywords: InsightResult & { vocabulary_size: number; excluded_place_names: number };
  review: ReviewState | null;
};
type NavPage = 'Archive' | 'Ingest' | 'Review queue' | 'Collections' | 'Datasets';
type SourceKind = 'files' | 'folder' | 'drive';

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

function cookie(name: string) {
  const item = document.cookie.split('; ').find((part) => part.startsWith(name + '='));
  return item ? decodeURIComponent(item.slice(name.length + 1)) : '';
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || 'GET').toUpperCase();
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', cookie('iihs_csrf'));
  const response = await fetch(path, { ...init, method, headers, credentials: 'same-origin' });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    throw new ApiError(result?.error?.message || 'The request could not be completed.', response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function formatSize(bytes: number) {
  return bytes < 1024 * 1024 ? Math.max(1, Math.round(bytes / 1024)) + ' KB' : (bytes / 1024 / 1024).toFixed(1) + ' MB';
}
function dateText(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value));
}
function dateTimeText(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
function formatTimestamp(ms: number | null | undefined) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '0:00';
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`;
}

const IIHS_LOGO_URL = 'https://iihs.co.in/wp-content/uploads/2026/07/IIHS-logo.svg';
const MOSAIC_TONES = ['#D8D5C4', '#ECE9D8', '#F2EFDE', '#E3E0CD', '#F7F5E8', '#E9E6D3'];

function IihsLogo({ variant = 'default' }: { variant?: 'default' | 'on-brick' }) {
  return (
    <img
      className={`iihs-logo ${variant === 'on-brick' ? 'iihs-logo-invert' : ''}`}
      src="/IIHS-logo.svg"
      onError={(e) => {
        if (e.currentTarget.src !== IIHS_LOGO_URL) {
          e.currentTarget.src = IIHS_LOGO_URL;
        }
      }}
      alt="Indian Institute for Human Settlements (IIHS)"
    />
  );
}

function SandMosaic({ cols = 20, rows = 4, seed = 42, className = '' }: { cols?: number; rows?: number; seed?: number; className?: string }) {
  const tiles = React.useMemo(() => {
    let s = seed;
    const rand = () => {
      s = (s * 1664525 + 1013904223) % 4294967296;
      return s / 4294967296;
    };
    return Array.from({ length: cols * rows }, () => MOSAIC_TONES[Math.floor(rand() * MOSAIC_TONES.length)]);
  }, [cols, rows, seed]);

  return (
    <div
      className={`sand-mosaic ${className}`.trim()}
      style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${rows}, 1fr)` }}
      aria-hidden="true"
    >
      {tiles.map((bg, idx) => (
        <i key={idx} style={{ background: bg }} />
      ))}
    </div>
  );
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [canCreateAdmin, setCanCreateAdmin] = useState<boolean | null>(null);
  const [canRegister, setCanRegister] = useState<boolean | null>(null);
  const [view, setView] = useState<'login' | 'register' | 'bootstrap'>('login');

  useEffect(() => {
    api<{ can_create_admin: boolean; can_register: boolean }>('/api/auth/bootstrap/status')
      .then((status) => {
        setCanCreateAdmin(status.can_create_admin);
        setCanRegister(status.can_register);
        if (status.can_create_admin) setView('bootstrap');
      })
      .catch(() => { setCanCreateAdmin(null); setCanRegister(null); });
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (view !== 'login' && password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setBusy(true);
    try {
      const route = view === 'bootstrap' ? 'bootstrap' : view === 'register' ? 'register' : 'login';
      const body = view === 'bootstrap' ? { username, password, setup_token: setupToken } : { username, password };
      const result = await api<{ user: User }>('/api/auth/' + route, { method: 'POST', body: JSON.stringify(body) });
      onLogin(result.user);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The local service is unavailable.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-hero">
        <div>
          <div className="auth-hero-top">
            <div className="brand-row">
              <IihsLogo />
              <div className="brand-copy"><strong>IIHS<span> / </span>ARCHIVE</strong><small>INDIAN INSTITUTE FOR HUMAN SETTLEMENTS</small></div>
            </div>
            <div className="cover-logo-box" title="Indian Institute for Human Settlements">
              <IihsLogo variant="on-brick" />
            </div>
          </div>
          <div className="eyebrow"><span className="eyebrow-line"/> THE INSTITUTIONAL MEMORY</div>
          <h1>Every place has<br/><em>a story to find.</em></h1>
          <p className="hero-subtitle">A local-first living archive of places, people, and the systems that shape how we live.</p>
        </div>
        <div className="auth-mosaic-wrap">
          <SandMosaic cols={24} rows={5} seed={42} />
          <div className="archive-footer">
            <span>IIHS MEDIA ARCHIVE · LOCAL WORKSPACE</span>
            <span><span className="footer-dot"/> Semantic search &amp; AI tagging</span>
          </div>
        </div>
      </section>
      <section className="auth-panel-wrap">
        <div className="auth-card">
          <div className="auth-tabs" role="tablist" aria-label="Authentication">
            <button type="button" role="tab" aria-selected={view === 'login'} className={view === 'login' ? 'active' : ''} onClick={() => { setView('login'); setError(''); }}>Sign in</button>
            {canRegister !== false && <button type="button" role="tab" aria-selected={view === 'register'} className={view === 'register' ? 'active' : ''} onClick={() => { setView('register'); setError(''); }}>Create account</button>}
            {canCreateAdmin !== false && <button type="button" role="tab" aria-selected={view === 'bootstrap'} className={view === 'bootstrap' ? 'active' : ''} onClick={() => { setView('bootstrap'); setError(''); }}>Create admin</button>}
          </div>
          <h2>{view === 'bootstrap' ? 'Set up administrator' : view === 'register' ? 'Create account' : 'Welcome back'}</h2>
          <p className="muted">{view === 'bootstrap' ? 'Enter the one-time setup key to initialize the first administrator.' : view === 'register' ? 'New accounts start with researcher access.' : 'Sign in to browse, search, and curate the archive.'}</p>
          <form onSubmit={submit}>
            <label>Username<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} minLength={3} maxLength={120} required/></label>
            <label>Password
              <span className="password-field">
                <input type={showPassword ? 'text' : 'password'} autoComplete={view === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(e) => setPassword(e.target.value)} minLength={view === 'login' ? 1 : 8} maxLength={72} required/>
                <button className="password-toggle" type="button" onClick={() => setShowPassword((s) => !s)}>{showPassword ? 'HIDE' : 'SHOW'}</button>
              </span>
            </label>
            {view !== 'login' && <label>Confirm password<input type={showPassword ? 'text' : 'password'} autoComplete="new-password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} minLength={8} maxLength={72} required/></label>}
            {view === 'bootstrap' && <label>One-time setup key<input autoComplete="off" value={setupToken} onChange={(e) => setSetupToken(e.target.value)} minLength={32} required/></label>}
            {error && <div className="alert" role="alert">{error}</div>}
            <button className="primary-button" disabled={busy}>{busy ? 'Please wait…' : view === 'bootstrap' ? 'Create administrator' : view === 'register' ? 'Create account' : 'Sign in'} <ArrowRight size={15}/></button>
          </form>
        </div>
      </section>
    </main>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState<NavPage>('Archive');
  const [mobileNav, setMobileNav] = useState(false);
  const [notice, setNotice] = useState('');
  const [summary, setSummary] = useState<{ assets: number; images: number; videos: number; indexed_assets: number; pending_reviews: number; queued_jobs: number; running_jobs: number } | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [tagFilter, setTagFilter] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);

  const showNotice = useCallback((message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(''), 3800);
  }, []);

  const loadSummary = useCallback(() => {
    api<NonNullable<typeof summary>>('/api/ops/summary').then(setSummary).catch(() => {});
    api<Tag[]>('/api/tags').then(setTags).catch(() => {});
  }, []);

  useEffect(() => {
    api<User>('/api/auth/me')
      .then((me) => { setUser(me); })
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, [refreshTick]);

  useEffect(() => {
    if (user) loadSummary();
  }, [user, refreshTick, loadSummary]);

  const logout = async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' });
      setUser(null);
      setPage('Archive');
    } catch (e) {
      showNotice((e as Error).message);
    }
  };

  if (checking) return <div className="page-content"><p className="muted">Connecting to the local archive service…</p></div>;
  if (!user) return <Login onLogin={(value) => { setUser(value); setRefreshTick((n) => n + 1); }}/>;

  const canIngest = ['admin', 'archivist'].includes(user.role);
  const canReview = ['admin', 'archivist'].includes(user.role);
  const navItems: { label: NavPage; icon: typeof LayoutGrid; count?: string }[] = [
    { label: 'Archive', icon: LayoutGrid, count: summary?.assets ? String(summary.assets) : undefined },
    ...(canIngest ? [{ label: 'Ingest' as NavPage, icon: Upload }] : []),
    { label: 'Review queue', icon: ScanSearch, count: summary?.pending_reviews ? String(summary.pending_reviews) : undefined },
    { label: 'Collections', icon: FolderClosed, count: tags.length ? String(tags.length) : undefined },
    { label: 'Datasets', icon: Database }
  ];

  const dotColors = ['rust', 'moss', 'blue', 'gold'];
  const collectionLinks = tags.length
    ? tags.slice(0, 6).map((t, i) => ({ name: t.display_name, color: dotColors[i % dotColors.length], tag: t.display_name }))
    : [
        { name: 'Bengaluru fieldwork', color: 'rust', tag: 'Fieldwork' },
        { name: 'Ecology & environment', color: 'moss', tag: 'Biodiversity' },
        { name: 'Built environment', color: 'blue', tag: 'Urban planning' },
        { name: 'Campus life', color: 'gold', tag: 'IIHS Kengeri Campus' }
      ];

  const indexedPct = summary && summary.assets > 0 ? Math.min(100, Math.round((summary.indexed_assets / summary.assets) * 100)) : 100;
  const initials = user.username.slice(0, 2).toUpperCase();

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
        <div className="brand-row">
          <IihsLogo />
          <div className="brand-copy"><strong>IIHS<span> / </span>ARCHIVE</strong><small>INDIAN INSTITUTE FOR HUMAN SETTLEMENTS</small></div>
          <button className="icon-button mobile-close" onClick={() => setMobileNav(false)} aria-label="Close menu"><X size={17}/></button>
        </div>
        <div className="workspace-pill">
          <div className="workspace-avatar">I</div>
          <div><strong>Research archive</strong><small>{user.mode === 'demo' ? 'Local workspace' : 'Restricted workspace'}</small></div>
          <ChevronDown size={14}/>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main navigation">
          {navItems.map(({ label, icon: Icon, count }) => (
            <button key={label} className={`nav-item ${page === label ? 'active' : ''}`} onClick={() => { setPage(label); setMobileNav(false); }}>
              <Icon size={17} strokeWidth={1.8}/><span>{label}</span>{count && <span className="nav-count">{count}</span>}
            </button>
          ))}
        </nav>
        <div className="nav-caption collection-caption">
          YOUR COLLECTIONS
          <button aria-label="Manage collections" onClick={() => { setPage('Collections'); setMobileNav(false); }}><Plus size={13}/></button>
        </div>
        {collectionLinks.map((c) => (
          <button key={c.name} className={`collection-link ${tagFilter === c.tag && page === 'Archive' ? 'active' : ''}`} onClick={() => { setTagFilter(tagFilter === c.tag ? '' : c.tag); setPage('Archive'); setMobileNav(false); }}>
            <span className={`collection-dot ${c.color}`}/>
            <span>{c.name}</span>
          </button>
        ))}
        <div className="sidebar-spacer"/>
        <div className="storage-card">
          <div className="storage-head"><span><HardDrive size={14}/> Local storage</span><span className="live-dot"/></div>
          <div className="storage-amount">{summary ? summary.assets.toLocaleString() : '—'} <small>items <span>({summary ? summary.indexed_assets.toLocaleString() : 0} indexed)</span></small></div>
          <div className="storage-bar"><span style={{ width: `${indexedPct}%` }}/></div>
          <div className="storage-foot"><span>{indexedPct}% indexed</span><span>No size cap</span></div>
        </div>
        <div className="user-row">
          <div className="user-avatar">{initials}</div>
          <div className="user-details"><strong>{user.username}</strong><small>{user.role}</small></div>
          <button className="icon-button" onClick={logout} title="Sign out" aria-label="Sign out"><LogOut size={16}/></button>
        </div>
      </aside>

      {mobileNav && <button className="mobile-scrim" onClick={() => setMobileNav(false)} aria-label="Close navigation"/>}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu size={20}/></button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14}/><strong>{page}</strong></div>
          <div className="topbar-right">
            <span className="local-badge"><span className="live-dot"/> LOCAL-FIRST</span>
            <button className="icon-button" aria-label="Help" onClick={() => showNotice('Click any media card to open the 3D metadata flip card with AI objects and IIHS keywords.')}><CircleHelp size={18}/></button>
            <button className="icon-button" aria-label="Settings" onClick={() => setPage('Datasets')}><Settings2 size={18}/></button>
            <div className="top-avatar" title={`${user.username} (${user.role})`}>{initials}</div>
          </div>
        </header>

        {page === 'Archive' && (
          <ArchiveView
            summary={summary}
            canIngest={canIngest}
            externalQuery={tagFilter}
            onClearExternalQuery={() => setTagFilter('')}
            onNavigateIngest={() => setPage('Ingest')}
            onSelectAsset={setSelected}
            showNotice={showNotice}
          />
        )}

        {page === 'Ingest' && (
          <IngestView
            onCompleted={() => { loadSummary(); setRefreshTick((n) => n + 1); }}
            showNotice={showNotice}
          />
        )}

        {page === 'Review queue' && (
          <ReviewQueueView
            canReview={canReview}
            onChanged={loadSummary}
            onNavigateIngest={() => setPage('Ingest')}
            showNotice={showNotice}
          />
        )}

        {page === 'Collections' && (
          <CollectionsView
            role={user.role}
            tags={tags}
            onReload={loadSummary}
            onPickTag={(label) => { setTagFilter(label); setPage('Archive'); }}
            showNotice={showNotice}
          />
        )}

        {page === 'Datasets' && (
          <DatasetsOpsView
            user={user}
            summary={summary}
            onRefresh={() => { loadSummary(); setRefreshTick((n) => n + 1); }}
            showNotice={showNotice}
          />
        )}

        <footer className="main-footer">
          <span>IIHS MEDIA ARCHIVE <span className="footer-sep">/</span> FIELD NOTES FOR THE FUTURE</span>
          <span><span className="footer-dot"/> Local archive · AI suggestions are never shown as human-confirmed</span>
        </footer>
      </main>

      {selected && (
        <AssetDetail
          asset={selected}
          role={user.role}
          onClose={() => setSelected(null)}
          onUpdated={() => { loadSummary(); }}
          showNotice={showNotice}
        />
      )}

      {notice && (
        <div role="status" className="toast">
          <span className="toast-mark"><Check size={14}/></span>
          {notice}
          <button onClick={() => setNotice('')} aria-label="Dismiss notification"><X size={14}/></button>
        </div>
      )}
    </div>
  );
}

function ArchiveView({
  summary,
  canIngest,
  externalQuery,
  onClearExternalQuery,
  onNavigateIngest,
  onSelectAsset,
  showNotice,
}: {
  summary: { assets: number; images: number; videos: number; indexed_assets: number; pending_reviews: number } | null;
  canIngest: boolean;
  externalQuery: string;
  onClearExternalQuery: () => void;
  onNavigateIngest: () => void;
  onSelectAsset: (asset: Asset) => void;
  showNotice: (msg: string) => void;
}) {
  const [data, setData] = useState<Page<Asset> | null>(null);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'semantic' | 'keyword'>('semantic');
  const [activeFilter, setActiveFilter] = useState<'All media' | 'Images' | 'Videos'>('All media');
  const [addedYear, setAddedYear] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const searchInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (externalQuery) {
      setQuery(externalQuery);
      setMode('keyword');
    }
  }, [externalQuery]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchInput.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const mediaType = activeFilter === 'Images' ? 'image' : activeFilter === 'Videos' ? 'video' : '';

  const runFetch = useCallback(async (next: string | null = null, currentQuery = query, currentMode = mode) => {
    setLoading(true); setError('');
    try {
      const params = new URLSearchParams({ limit: '32' });
      const trimmed = currentQuery.trim();
      if (trimmed) {
        params.set('q', trimmed);
        params.set('mode', currentMode);
      }
      if (mediaType) params.set('media_type', mediaType);
      if (addedYear) params.set('added_year', addedYear);
      if (next) params.set('cursor', next);
      const path = trimmed ? '/api/assets/search?' : '/api/assets?';
      const result = await api<Page<Asset>>(path + params.toString());
      setData((old) => (next && old ? { items: old.items.concat(result.items), next_cursor: result.next_cursor } : result));
      setCursor(result.next_cursor);
    } catch (e) {
      if (currentMode === 'semantic' && currentQuery.trim()) {
        try {
          const fallback = new URLSearchParams({ limit: '32', q: currentQuery.trim(), mode: 'keyword' });
          if (mediaType) fallback.set('media_type', mediaType);
          if (addedYear) fallback.set('added_year', addedYear);
          const res = await api<Page<Asset>>('/api/assets/search?' + fallback.toString());
          setData(res);
          setCursor(res.next_cursor);
          showNotice('Local embedding bridge is offline; showing exact keyword matches instead.');
          return;
        } catch {
          // fall through
        }
      }
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [query, mode, mediaType, addedYear, showNotice]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      runFetch(null, query, mode);
    }, query.trim() ? 260 : 0);
    return () => window.clearTimeout(timer);
  }, [query, mode, mediaType, addedYear, externalQuery]);

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    setCursor(null);
    runFetch(null, query, mode);
  }

  const items = data?.items || [];

  return (
    <section className="page-content">
      <div className="eyebrow"><span className="eyebrow-line"/> THE INSTITUTIONAL MEMORY</div>
      <div className="hero-row">
        <div>
          <h1>Every place has<br/><em>a story to find.</em></h1>
          <p className="hero-subtitle">A living archive of places, people, and the systems that shape how we live.</p>
        </div>
        {canIngest && (
          <button className="primary-button hero-ingest" onClick={onNavigateIngest}>
            <Plus size={16}/> Add to archive <ArrowUpRight size={15}/>
          </button>
        )}
      </div>

      <form className="search-panel" onSubmit={submitSearch}>
        <div className="search-icon"><Sparkles size={18}/></div>
        <div className="search-field">
          <label htmlFor="archive-search">SEARCH BY MEMORY, NOT JUST METADATA</label>
          <input
            ref={searchInput}
            id="archive-search"
            value={query}
            onChange={(e) => { setQuery(e.target.value); if (externalQuery) onClearExternalQuery(); }}
            placeholder="Try “a crowded street after the rain”, a printed name, or an IIHS keyword…"
          />
        </div>
        {query && (
          <button type="button" className="icon-button" aria-label="Clear search" onClick={() => { setQuery(''); onClearExternalQuery(); runFetch(null, '', mode); }}>
            <X size={15}/>
          </button>
        )}
        <kbd><Command size={12}/> K</kbd>
        <button type="submit" className="search-action" disabled={loading} aria-label="Search">
          <ArrowRight size={18}/>
        </button>
      </form>
      <div className="search-hint">
        <Sparkles size={12}/> Powered by local Google EmbeddingGemma 2 semantic search <span>·</span>
        <span className="muted">Mode: {mode === 'semantic' ? 'By visual meaning' : 'Exact text (filenames, OCR, tags, transcripts)'}</span>
      </div>

      <div className="stats-row">
        <div className="stat-item">
          <span className="stat-label">ARCHIVE ITEMS</span>
          <div className="stat-value">{summary ? summary.assets.toLocaleString() : '—'} <span className="stat-delta">{summary ? `${summary.images} photos · ${summary.videos} videos` : 'live'}</span></div>
        </div>
        <div className="stat-item">
          <span className="stat-label">SEMANTICALLY INDEXED</span>
          <div className="stat-value">{summary ? summary.indexed_assets.toLocaleString() : '—'} <span className="stat-muted">vector embeddings</span></div>
        </div>
        <div className="stat-item">
          <span className="stat-label">NEEDS REVIEW</span>
          <div className="stat-value">{summary ? summary.pending_reviews.toLocaleString() : '0'} <span className="stat-review">Awaiting a human</span></div>
        </div>
        <div className="stat-item">
          <span className="stat-label">INDEX HEALTH</span>
          <div className="stat-value"><span className="health-dot"/> Ready <span className="stat-muted">Local index</span></div>
        </div>
      </div>

      <div className="section-heading">
        <div>
          <div className="eyebrow small-eyebrow">EXPLORE THE ARCHIVE</div>
          <h2>{query.trim() ? <>Search results <span>↘</span></> : <>Recently collected <span>↘</span></>}</h2>
        </div>
        {query.trim() && (
          <button className="text-button" onClick={() => { setQuery(''); onClearExternalQuery(); runFetch(null, '', mode); }}>
            Reset search <ArrowRight size={15}/>
          </button>
        )}
      </div>

      <div className="filter-bar">
        <div className="filter-tabs">
          {(['All media', 'Images', 'Videos'] as const).map((filter) => (
            <button key={filter} type="button" onClick={() => setActiveFilter(filter)} className={activeFilter === filter ? 'selected' : ''}>
              {filter}
              {filter === 'All media' && summary && <span className="filter-total">{summary.assets}</span>}
              {filter === 'Images' && summary && <span className="filter-total">{summary.images}</span>}
              {filter === 'Videos' && summary && <span className="filter-total">{summary.videos}</span>}
            </button>
          ))}
        </div>
        <div className="filter-actions">
          <span className="results-count">{items.length} {items.length === 1 ? 'item' : 'items'} shown</span>
          <button type="button" className={`filter-button ${showFilters ? 'active' : ''}`} onClick={() => setShowFilters((s) => !s)}>
            <SlidersHorizontal size={15}/> Filters
          </button>
          <div className="view-toggle">
            <button type="button" className={view === 'grid' ? 'selected' : ''} onClick={() => setView('grid')} aria-label="Grid view"><LayoutGrid size={15}/></button>
            <button type="button" className={view === 'list' ? 'selected' : ''} onClick={() => setView('list')} aria-label="List view"><ListFilter size={15}/></button>
          </div>
        </div>
      </div>

      {showFilters && (
        <div className="filter-drawer">
          <label>
            Search mode
            <select value={mode} onChange={(e) => { const next = e.target.value as 'semantic' | 'keyword'; setMode(next); if (query.trim()) runFetch(null, query, next); }}>
              <option value="semantic">By visual meaning (EmbeddingGemma 2)</option>
              <option value="keyword">Exact text (filename, OCR, transcript, tags)</option>
            </select>
          </label>
          <label>
            Added year
            <select value={addedYear} onChange={(e) => setAddedYear(e.target.value)}>
              <option value="">Any year</option>
              {Array.from({ length: 20 }, (_, i) => String(new Date().getFullYear() - i)).map((year) => (
                <option key={year} value={year}>{year}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {error && <div className="alert" role="alert">{error}</div>}

      {loading && !data ? (
        <div className="empty-feature-card">
          <SandMosaic cols={6} rows={6} seed={7} className="empty-mosaic"/>
          <div className="empty-feature-icon"><LoaderCircle className="spin"/></div>
          <div><h2>Loading archive…</h2><p>Fetching records from the local media database.</p></div>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-feature-card">
          <SandMosaic cols={6} rows={6} seed={19} className="empty-mosaic"/>
          <div className="empty-feature-icon"><ImageIcon/></div>
          <div>
            <h2>{query.trim() ? 'No matching media found' : 'No media in the archive yet'}</h2>
            <p>{query.trim() ? 'Try a broader description, switch between meaning and exact text in Filters, or clear the search query.' : 'Import photographs or videos from your computer to index them automatically.'}</p>
            {canIngest && <button className="secondary-button" onClick={onNavigateIngest}>Open ingestion panel <ArrowRight size={15}/></button>}
          </div>
        </div>
      ) : (
        <div className={`asset-grid ${view === 'list' ? 'asset-list' : ''}`}>
          {items.map((asset, index) => {
            const thumbSrc = asset.media_type === 'video' && asset.matched_frame_id
              ? `/api/assets/${asset.id}/frames/${asset.matched_frame_id}`
              : asset.has_thumbnail
                ? `/api/assets/${asset.id}/thumbnail`
                : '';
            const hasMatchedFrame = asset.media_type === 'video' && asset.matched_timestamp_ms !== null && asset.matched_timestamp_ms !== undefined;
            return (
              <button key={asset.id} className={`asset-card ${hasMatchedFrame ? 'asset-card-matched' : ''}`} onClick={() => onSelectAsset(asset)}>
                <div className="asset-thumb">
                  {thumbSrc ? (
                    <img src={thumbSrc} alt={asset.original_filename} loading="lazy"/>
                  ) : (
                    <span className="thumb-placeholder">
                      {asset.media_type === 'video' ? <Video size={22}/> : <ImageIcon size={22}/>}
                      <span>{asset.media_type.toUpperCase()}</span>
                    </span>
                  )}
                  <span className="asset-kind">
                    {asset.media_type === 'video' ? <Video size={12}/> : <ImageIcon size={12}/>}
                    {asset.media_type === 'video' ? 'VIDEO' : 'PHOTO'}
                  </span>
                  <span className="asset-index">{String(index + 1).padStart(2, '0')}</span>
                  {asset.media_type === 'video' && (hasMatchedFrame || asset.duration_ms !== null) && (
                    <span className={`video-time-pill ${hasMatchedFrame ? 'matched' : ''}`}>
                      {hasMatchedFrame ? `▸ ${formatTimestamp(asset.matched_timestamp_ms)}` : formatTimestamp(asset.duration_ms)}
                    </span>
                  )}
                  <span className="asset-open"><ArrowUpRight size={17}/></span>
                </div>
                <div className="asset-info">
                  <div className="asset-meta">
                    <span>
                      {asset.media_type === 'video' && asset.duration_ms ? `${formatTimestamp(asset.duration_ms)} · ` : asset.width ? `${asset.width} × ${asset.height} · ` : ''}
                      {formatSize(asset.size_bytes)}
                    </span>
                    <span>{dateText(asset.created_at)}</span>
                  </div>
                  <h3>{asset.original_filename}</h3>
                  {hasMatchedFrame && (
                    <div className="video-match-row">
                      <span className="video-match-badge">
                        <ScanSearch size={13}/> Matches the frame at {formatTimestamp(asset.matched_timestamp_ms)}
                      </span>
                      <span className="open-at-pill">Open at {formatTimestamp(asset.matched_timestamp_ms)}</span>
                    </div>
                  )}
                  <div className="tag-row">
                    {asset.annotations.slice(0, 3).map((a) => (
                      <span key={a.id} className={`tag ${a.status}`}>{a.label}</span>
                    ))}
                    {asset.annotations.length === 0 && !hasMatchedFrame && <span className="tag">Click for AI insights</span>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {data?.next_cursor && (
        <button className="secondary-button load-more" disabled={loading} onClick={() => runFetch(cursor, query, mode)}>
          {loading ? 'Loading…' : 'Load more media'} <ArrowRight size={15}/>
        </button>
      )}

      <div className="archive-footer">
        <span>Showing {items.length} of {summary?.assets ?? items.length} catalogued items</span>
        <span><span className="footer-dot"/> Click any card to flip &amp; inspect metadata, objects, and IIHS keywords</span>
      </div>
    </section>
  );
}

function IngestView({ onCompleted, showNotice }: { onCompleted: () => void; showNotice: (msg: string) => void }) {
  const [source, setSource] = useState<SourceKind>('files');
  const [recursive, setRecursive] = useState(true);
  const [metadata, setMetadata] = useState(true);
  const [thumbnails, setThumbnails] = useState(true);
  const [tags, setTags] = useState(true);
  const [files, setFiles] = useState<File[]>([]);
  const [serverSources, setServerSources] = useState<{ id: string; name: string; demo: boolean; enabled: boolean }[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const requestKey = useRef('');

  const reloadJobs = useCallback(async () => {
    try {
      const list = await api<Job[]>('/api/jobs');
      setJobs(list);
      setSelectedJob((old) => {
        if (!old && list.length) return list[0];
        if (!old) return null;
        const match = list.find((j) => j.id === old.id);
        return match ? { ...old, ...match } : old;
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reloadJobs();
    api<{ id: string; name: string; demo: boolean; enabled: boolean }[]>('/api/sources')
      .then((list) => { setServerSources(list); if (list[0]) setSelectedSourceId(list[0].id); })
      .catch(() => {});
  }, [reloadJobs]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      reloadJobs();
      onCompleted();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [reloadJobs, onCompleted]);

  const handlePickFiles = (picked: FileList | null) => {
    if (!picked || picked.length === 0) return;
    const arr = Array.from(picked);
    setFiles(arr);
    setError('');
    showNotice(`${arr.length} ${arr.length === 1 ? 'file' : 'files'} staged (${formatSize(arr.reduce((sum, f) => sum + f.size, 0))}). Ready to import.`);
  };

  async function startImport() {
    setError('');
    if (source === 'drive') {
      if (!selectedSourceId) {
        setError('No approved server source is configured.');
        return;
      }
      setBusy(true);
      try {
        const key = Array.from(crypto.getRandomValues(new Uint8Array(16))).map((v) => v.toString(16).padStart(2, '0')).join('');
        const job = await api<Job>('/api/ingestions/previews', {
          method: 'POST',
          headers: { 'Idempotency-Key': key },
          body: JSON.stringify({ source_id: selectedSourceId, max_files: 500 }),
        });
        const full = await api<Job>('/api/jobs/' + job.id);
        setSelectedJob(full);
        await reloadJobs();
        showNotice('Server folder preview started.');
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }

    if (!files.length) {
      (source === 'folder' ? folderInput.current : fileInput.current)?.click();
      return;
    }
    setBusy(true);
    if (!requestKey.current) {
      requestKey.current = Array.from(crypto.getRandomValues(new Uint8Array(16))).map((v) => v.toString(16).padStart(2, '0')).join('');
    }
    const body = new FormData();
    files.forEach((file) => body.append('files', file, file.name));
    try {
      const job = await api<Job>('/api/ingestions/imports', {
        method: 'POST',
        headers: { 'Idempotency-Key': requestKey.current },
        body,
      });
      requestKey.current = '';
      setFiles([]);
      if (fileInput.current) fileInput.current.value = '';
      if (folderInput.current) folderInput.current.value = '';
      const full = await api<Job>('/api/jobs/' + job.id);
      setSelectedJob(full);
      await reloadJobs();
      onCompleted();
      showNotice('Import started. Media is being validated and indexed automatically.');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function jobAction(job: Job, actionName: 'confirm' | 'cancel' | 'retry') {
    setBusy(true); setError('');
    try {
      const updated = await api<Job>(`/api/jobs/${job.id}/${actionName}`, { method: 'POST', body: '{}' });
      setSelectedJob(updated);
      await reloadJobs();
      onCompleted();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const totalBytes = files.reduce((s, f) => s + f.size, 0);
  const sourceSummary = files.length === 1 ? files[0].name : files.length > 1 ? `${files[0].name} + ${files.length - 1} more (${formatSize(totalBytes)})` : '';
  const activeStep = selectedJob && ['running', 'queued'].includes(selectedJob.state) ? 3 : files.length || selectedJob?.state === 'preview_ready' ? 2 : 1;

  return (
    <section className="page-content ingest-page">
      <div className="eyebrow"><span className="eyebrow-line"/> ARCHIVE WORKFLOW / 01</div>
      <div className="ingest-title-row">
        <div>
          <h1>Bring something<br/><em>into the archive.</em></h1>
          <p className="hero-subtitle">Select files or a folder of any size—media is validated, embedded with EmbeddingGemma 2, and made searchable automatically.</p>
        </div>
        <span className="workflow-pill"><ShieldCheck size={15}/> Safe by design · No size limit</span>
      </div>

      <div className="stepper">
        <div className="step active"><span>01</span><div><strong>Choose source</strong><small>Where the media lives</small></div></div>
        <div className="step-line"/>
        <div className={`step ${activeStep >= 2 ? 'active' : ''}`}><span>02</span><div><strong>Validate &amp; stage</strong><small>Check formats &amp; hashes</small></div></div>
        <div className="step-line"/>
        <div className={`step ${activeStep >= 3 ? 'active' : ''}`}><span>03</span><div><strong>Ingest &amp; index</strong><small>Process and embed media</small></div></div>
      </div>

      {error && <div className="alert" role="alert">{error}</div>}

      <div className="ingest-layout">
        <div className="ingest-main-card">
          <div className="card-head">
            <div>
              <span className="card-kicker">STEP 01 / SOURCE</span>
              <h2>Where should we look?</h2>
              <p>Choose files, a folder, or an approved server mount. There is no file count or file size limit.</p>
            </div>
            <span className="card-icon"><FolderOpen size={19}/></span>
          </div>

          <div className="source-options">
            <button type="button" className={`source-option ${source === 'files' ? 'chosen' : ''}`} onClick={() => { setSource('files'); setFiles([]); }}>
              <span className="source-icon"><FileImage size={20}/></span>
              <strong>Specific files</strong>
              <small>Select images or videos of any size</small>
              <span className="radio-circle">{source === 'files' && <span/>}</span>
            </button>
            <button type="button" className={`source-option ${source === 'folder' ? 'chosen' : ''}`} onClick={() => { setSource('folder'); setFiles([]); }}>
              <span className="source-icon"><FolderClosed size={20}/></span>
              <strong>A folder</strong>
              <small>Include subfolders from your computer</small>
              <span className="radio-circle">{source === 'folder' && <span/>}</span>
            </button>
            <button type="button" className={`source-option ${source === 'drive' ? 'chosen' : ''}`} onClick={() => { setSource('drive'); setFiles([]); }}>
              <span className="source-icon"><HardDrive size={20}/></span>
              <strong>Server drive</strong>
              <small>Approved archive mounts</small>
              <span className="radio-circle">{source === 'drive' && <span/>}</span>
            </button>
          </div>

          <input ref={fileInput} type="file" multiple hidden accept=".jpg,.jpeg,.png,.webp,.tif,.tiff,.mp4,.mov,.m4v,image/*,video/mp4,video/quicktime" onChange={(e) => handlePickFiles(e.target.files)}/>
          <input ref={folderInput} type="file" multiple hidden {...({ webkitdirectory: '' } as React.InputHTMLAttributes<HTMLInputElement>)} onChange={(e) => handlePickFiles(e.target.files)}/>

          {source === 'drive' ? (
            <div className="server-note">
              <ShieldCheck size={17}/>
              <div style={{ flex: 1 }}>
                <strong>Server storage is permission-controlled</strong>
                <p>Select an administrator-approved mount below to run a bounded preview scan before importing.</p>
                {serverSources.length > 0 && (
                  <select value={selectedSourceId} onChange={(e) => setSelectedSourceId(e.target.value)} style={{ marginTop: 8, padding: '6px 10px', borderRadius: 4, border: '1px solid #cfd3c4', background: '#fffefa', fontSize: 11 }}>
                    {serverSources.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.demo ? 'demo' : 'approved'})</option>)}
                  </select>
                )}
              </div>
            </div>
          ) : (
            <button type="button" className="dropzone" onClick={() => (source === 'folder' ? folderInput.current : fileInput.current)?.click()}>
              <span className="drop-icon"><Upload size={21}/></span>
              <strong>{sourceSummary || (source === 'folder' ? 'Choose a folder to import' : 'Choose files to add')}</strong>
              <small>{sourceSummary ? 'Staged locally · Click Import & index below to begin' : 'JPG, PNG, WebP, TIFF, MP4, MOV, M4V · Unrestricted file size'}</small>
              <span className="browse-button">Browse {source === 'folder' ? 'folder' : 'files'} <ArrowRight size={14}/></span>
            </button>
          )}

          <div className="options-block">
            <div className="options-title">
              <div><h3>Processing preferences</h3><p>Active pipeline stages during ingestion.</p></div>
              <button type="button" className="quiet-icon" onClick={() => { setRecursive(true); setMetadata(true); setThumbnails(true); setTags(true); }} aria-label="Reset options"><RotateCcw size={15}/></button>
            </div>
            <Toggle title="Scan subfolders" description="Include nested directories when picking a folder" checked={recursive} onChange={setRecursive}/>
            <Toggle title="Extract metadata" description="Capture timestamps, dimensions, EXIF, and SHA-256 checksums" checked={metadata} onChange={setMetadata}/>
            <Toggle title="Generate thumbnails" description="Create fast WebP previews and sampled video frames" checked={thumbnails} onChange={setThumbnails}/>
            <Toggle title="Suggest AI tags & embeddings" description="Index with EmbeddingGemma 2, OCR, Whisper, and IIHS keywords" checked={tags} onChange={setTags} badge="LOCAL AI"/>
          </div>

          <div className="card-bottom">
            <div className="secure-note"><ShieldCheck size={15}/><span>Originals remain unchanged · No import size limit</span></div>
            <button type="button" className="primary-button" disabled={busy} onClick={startImport}>
              {busy ? <><LoaderCircle size={15} className="spin"/> Uploading &amp; indexing…</> : source === 'drive' ? <>Preview server folder <ArrowRight size={16}/></> : <>Import &amp; index media <ArrowRight size={16}/></>}
            </button>
          </div>
        </div>

        <aside className="ingest-side">
          <div className="side-card">
            <span className="card-kicker">BEFORE YOU START</span>
            <h3>A little context goes a long way.</h3>
            <div className="checklist-item"><Check size={15}/><span>No file size or count limit</span></div>
            <div className="checklist-item"><Check size={15}/><span>Automatic SHA-256 duplicate check</span></div>
            <div className="checklist-item"><Check size={15}/><span>EXIF &amp; video frame extraction</span></div>
            <div className="checklist-item"><Check size={15}/><span>700 IIHS keywords ready for ranking</span></div>
          </div>
          <div className="side-card model-card">
            <div className="model-heading"><span className="model-spark"><Sparkles size={16}/></span><span className="card-kicker">LOCAL EMBEDDINGS</span></div>
            <h3>Search by meaning, not filenames.</h3>
            <p>EmbeddingGemma 2 and Whisper small index images, video frames, OCR, and speech locally.</p>
            <div className="model-state"><span className="status-dot"/> Automatic indexing <strong>Active</strong></div>
          </div>
          <div className="privacy-note"><ShieldCheck size={15}/><span>Files go directly to your local archive storage. Your computer’s folder paths are never exposed.</span></div>
        </aside>
      </div>

      {selectedJob && (
        <div className="preview-panel">
          <div className="preview-heading">
            <div>
              <span className="card-kicker">ACTIVE JOB · {selectedJob.kind.toUpperCase()}</span>
              <h2>{selectedJob.state.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</h2>
              <p>Progress: {selectedJob.progress.done} of {selectedJob.progress.total} items processed</p>
            </div>
            <span className="preview-ready">
              {['running', 'queued'].includes(selectedJob.state) ? <LoaderCircle size={15} className="spin"/> : <CircleCheck size={15}/>}
              {selectedJob.state.replace(/_/g, ' ')}
            </span>
          </div>
          {Object.keys(selectedJob.summary || {}).length > 0 && (
            <div className="preview-stats">
              {Object.entries(selectedJob.summary).slice(0, 4).map(([k, v]) => (
                <div key={k}><span>{k.replace(/_/g, ' ').toUpperCase()}</span><strong>{String(v)}</strong></div>
              ))}
            </div>
          )}
          {selectedJob.error && <div className="preview-warning"><AlertTriangle size={16}/><span>{selectedJob.error}</span></div>}
          {selectedJob.preview_files && selectedJob.preview_files.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>FILE</th><th>TYPE</th><th>SIZE</th><th>STATE</th></tr></thead>
                <tbody>
                  {selectedJob.preview_files.map((file, i) => (
                    <tr key={i}>
                      <td>{file.path}</td>
                      <td>{file.type}</td>
                      <td>{formatSize(file.size_bytes)}</td>
                      <td><span className="state-chip">{file.state}</span>{file.message ? ` · ${file.message}` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="preview-actions">
            {selectedJob.state === 'preview_ready' && <button className="primary-button" disabled={busy} onClick={() => jobAction(selectedJob, 'confirm')}>Confirm &amp; ingest scope <ArrowRight size={15}/></button>}
            {['queued', 'running', 'cancel_requested'].includes(selectedJob.state) && <button className="secondary-button" disabled={busy} onClick={() => jobAction(selectedJob, 'cancel')}>Cancel job</button>}
            {['failed', 'partially_completed'].includes(selectedJob.state) && <button className="secondary-button" disabled={busy} onClick={() => jobAction(selectedJob, 'retry')}>Retry failed items</button>}
          </div>
        </div>
      )}

      {jobs.length > 0 && (
        <div className="preview-panel">
          <span className="card-kicker">RECENT IMPORTS</span>
          <div className="table-wrap">
            <table>
              <thead><tr><th>JOB</th><th>STATE</th><th>PROGRESS</th><th>CREATED</th></tr></thead>
              <tbody>
                {jobs.slice(0, 10).map((job) => (
                  <tr key={job.id} className={selectedJob?.id === job.id ? 'row-selected' : ''}>
                    <td><button type="button" className="table-link" onClick={() => api<Job>('/api/jobs/' + job.id).then(setSelectedJob)}>{job.kind} · {job.id.slice(0, 8)}</button></td>
                    <td><span className="state-chip">{job.state.replace(/_/g, ' ')}</span></td>
                    <td>{job.progress.done}/{job.progress.total}</td>
                    <td>{dateTimeText(job.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function ReviewQueueView({
  canReview,
  onChanged,
  onNavigateIngest,
  showNotice,
}: {
  canReview: boolean;
  onChanged: () => void;
  onNavigateIngest: () => void;
  showNotice: (msg: string) => void;
}) {
  const [items, setItems] = useState<{ id: string; asset_id: string; filename: string; label: string; source: string; confidence: number | null; media_type: string }[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const reload = useCallback(() => {
    if (!canReview) { setItems([]); return; }
    api<NonNullable<typeof items>>('/api/review/queue?limit=100').then(setItems).catch((e) => setError(e.message));
  }, [canReview]);

  useEffect(() => { reload(); }, [reload]);

  async function decide(id: string, decision: 'accept' | 'reject') {
    setBusy(id); setError('');
    try {
      await api(`/api/annotations/${id}/review`, { method: 'POST', body: JSON.stringify({ decision }) });
      setItems((old) => (old || []).filter((item) => item.id !== id));
      onChanged();
      showNotice(decision === 'accept' ? 'Suggestion accepted as a confirmed tag.' : 'Suggestion rejected.');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  }

  const groups = (items || []).reduce<Record<string, NonNullable<typeof items>>>((acc, item) => {
    (acc[item.asset_id] ||= []).push(item);
    return acc;
  }, {});

  return (
    <section className="page-content secondary-page">
      <div className="eyebrow"><span className="eyebrow-line"/> WORKSPACE / HUMAN REVIEW</div>
      <h1>A second pair<br/><em>of eyes.</em></h1>
      <p className="hero-subtitle">AI suggestions sent from a media flip card appear here so archivists and administrators can verify them.</p>
      {error && <div className="alert" role="alert">{error}</div>}
      {!canReview ? (
        <div className="empty-feature-card">
          <SandMosaic cols={6} rows={6} seed={31} className="empty-mosaic"/>
          <div className="empty-feature-icon"><ScanSearch/></div>
          <div>
            <h2>Archivist or administrator access required</h2>
            <p>Researchers can send any media card to review using the “Send to review” button on the flip card. Archivists and administrators approve or reject queued suggestions here.</p>
          </div>
        </div>
      ) : !items ? (
        <div className="empty-feature-card"><SandMosaic cols={6} rows={6} seed={11} className="empty-mosaic"/><div className="empty-feature-icon"><LoaderCircle className="spin"/></div><div><h2>Loading review queue…</h2></div></div>
      ) : items.length === 0 ? (
        <div className="empty-feature-card">
          <SandMosaic cols={6} rows={6} seed={23} className="empty-mosaic"/>
          <div className="empty-feature-icon"><CheckCircle2/></div>
          <div>
            <h2>Nothing waiting for review</h2>
            <p>Open any image or video in the Archive and click “Send to review” on the back of its flip card to queue its AI objects and IIHS keywords for verification.</p>
            <button className="secondary-button" onClick={onNavigateIngest}>Open ingestion panel <ArrowRight size={15}/></button>
          </div>
        </div>
      ) : (
        <div className="review-list">
          {Object.entries(groups).map(([assetId, rows]) => (
            <section className="review-group" key={assetId}>
              <div className="review-thumb"><img src={`/api/assets/${assetId}/thumbnail`} alt={rows[0].filename}/></div>
              <div>
                <h2>{rows[0].filename}</h2>
                <ul>
                  {rows.map((row) => (
                    <li key={row.id}>
                      <span>{row.label}{row.confidence !== null && <small> · {Math.round(row.confidence * 100)}%</small>}</span>
                      <span className="review-buttons">
                        <button className="secondary-button" disabled={busy === row.id} onClick={() => decide(row.id, 'accept')}>Accept</button>
                        <button className="text-button danger-text" disabled={busy === row.id} onClick={() => decide(row.id, 'reject')}>Reject</button>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function CollectionsView({
  role,
  tags,
  onReload,
  onPickTag,
  showNotice,
}: {
  role: Role;
  tags: Tag[];
  onReload: () => void;
  onPickTag: (label: string) => void;
  showNotice: (msg: string) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');

  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    try {
      await api('/api/tags', { method: 'POST', body: JSON.stringify({ display_name: name, description }) });
      setName(''); setDescription('');
      onReload();
      showNotice('Collection tag added to the controlled vocabulary.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="page-content secondary-page">
      <div className="eyebrow"><span className="eyebrow-line"/> WORKSPACE / COLLECTIONS &amp; VOCABULARY</div>
      <h1>Knowledge, in<br/><em>good company.</em></h1>
      <p className="hero-subtitle">Gather related media into purposeful collections and canonical IIHS vocabulary labels.</p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="two-column">
        {['admin', 'archivist'].includes(role) ? (
          <form className="form-card" onSubmit={submit}>
            <span className="card-kicker">NEW COLLECTION / TAG</span>
            <h2>Add a vocabulary label</h2>
            <label>Display name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={160} placeholder="e.g. Urban Fellows Programme" required/></label>
            <label>Description <small>(optional)</small><textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} rows={3}/></label>
            <button className="primary-button">Add to vocabulary <Plus size={15}/></button>
          </form>
        ) : (
          <div className="form-card">
            <span className="card-kicker">CONTROLLED VOCABULARY</span>
            <h2>700 IIHS institutional keywords</h2>
            <p className="muted">Every image is automatically ranked against 700 IIHS institutional keywords (with city, state, and place names excluded). Click any active label on the right to filter the archive.</p>
          </div>
        )}
        <section className="form-card">
          <span className="card-kicker">ACTIVE COLLECTIONS &amp; LABELS ({tags.length})</span>
          <h2>Browse by label</h2>
          {tags.length ? (
            <ul className="tag-list">
              {tags.map((tag) => (
                <li key={tag.id}>
                  <div>
                    <strong>{tag.display_name}</strong>{' '}
                    <code>{tag.canonical_name}</code>
                    {tag.description && <div className="muted">{tag.description}</div>}
                  </div>
                  <button type="button" className="secondary-button" onClick={() => onPickTag(tag.display_name)}>
                    View in archive <ArrowRight size={14}/>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No custom labels created yet. Accepting AI suggestions in the Review queue or adding a tag on a media card will populate this list.</p>
          )}
        </section>
      </div>
    </section>
  );
}

function DatasetsOpsView({
  user,
  summary,
  onRefresh,
  showNotice,
}: {
  user: User;
  summary: { assets: number; images: number; videos: number; indexed_assets: number; pending_reviews: number; queued_jobs: number; running_jobs: number } | null;
  onRefresh: () => void;
  showNotice: (msg: string) => void;
}) {
  const [audit, setAudit] = useState<{ id: string; action: string; target_type: string; target_id: string | null; result: string; created_at: string }[]>([]);
  const [capabilities, setCapabilities] = useState<any>(null);
  const [accounts, setAccounts] = useState<(User & { active: boolean })[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newRole, setNewRole] = useState<Role>('researcher');
  const [roleEdits, setRoleEdits] = useState<Record<string, Role>>({});
  const [error, setError] = useState('');

  const reload = useCallback(() => {
    if (['admin', 'operator'].includes(user.role)) {
      api<typeof audit>('/api/ops/audit').then(setAudit).catch(() => {});
      api<any>('/api/ops/capabilities').then(setCapabilities).catch(() => {});
    }
    if (user.role === 'admin') {
      api<(User & { active: boolean })[]>('/api/auth/users').then(setAccounts).catch(() => {});
    }
  }, [user.role]);

  useEffect(() => { reload(); }, [reload]);

  async function createUser(e: FormEvent) {
    e.preventDefault(); setError('');
    try {
      await api('/api/auth/users', { method: 'POST', body: JSON.stringify({ username, password, role: newRole }) });
      setUsername(''); setPassword('');
      reload(); onRefresh();
      showNotice('Account created.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function changeRole(id: string) {
    const next = roleEdits[id];
    if (!next) return;
    setError('');
    try {
      await api(`/api/auth/users/${id}/role`, { method: 'POST', body: JSON.stringify({ role: next }) });
      reload(); onRefresh();
      showNotice('Role updated.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function revokeUser(id: string) {
    setError('');
    try {
      await api(`/api/auth/users/${id}/revoke`, { method: 'POST', body: '{}' });
      reload();
      showNotice('Account revoked.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="page-content secondary-page">
      <div className="eyebrow"><span className="eyebrow-line"/> WORKSPACE / DATASETS, OPERATIONS &amp; ACCESS</div>
      <h1>From image to<br/><em>insight.</em></h1>
      <p className="hero-subtitle">System telemetry, local AI model capabilities, audit logs, and workspace access control.</p>

      <div className="stats-row">
        <div className="stat-item"><span className="stat-label">MEDIA RECORDS</span><div className="stat-value">{summary?.assets ?? 0}</div></div>
        <div className="stat-item"><span className="stat-label">SEMANTICALLY INDEXED</span><div className="stat-value">{summary?.indexed_assets ?? 0}</div></div>
        <div className="stat-item"><span className="stat-label">QUEUED JOBS</span><div className="stat-value">{summary?.queued_jobs ?? 0}</div></div>
        <div className="stat-item"><span className="stat-label">ACTIVE JOBS</span><div className="stat-value">{summary?.running_jobs ?? 0}</div></div>
      </div>

      {error && <div className="alert" role="alert">{error}</div>}

      {capabilities && (
        <div className="preview-panel">
          <span className="card-kicker">LOCAL AI CAPABILITIES</span>
          <div className="preview-stats">
            <div><span>EMBEDDINGGEMMA 2</span><strong>{capabilities.local_ai}</strong></div>
            <div><span>SEMANTIC SEARCH</span><strong>{capabilities.semantic_search}</strong></div>
            <div><span>VIDEO &amp; WHISPER</span><strong>{capabilities.video_processing}</strong></div>
            <div><span>IIHS KEYWORDS</span><strong>700 active (places excluded)</strong></div>
          </div>
        </div>
      )}

      {user.role === 'admin' && (
        <div className="two-column">
          <form className="form-card" onSubmit={createUser}>
            <span className="card-kicker">ACCESS CONTROL</span>
            <h2>Create an account</h2>
            <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} minLength={3} maxLength={120} required/></label>
            <label>Temporary password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} maxLength={72} required/><small>At least 8 characters.</small></label>
            <label>Role
              <select value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
                <option value="archivist">Archivist</option>
                <option value="researcher">Researcher</option>
                <option value="operator">Operator</option>
                <option value="admin">Administrator</option>
              </select>
            </label>
            <button className="primary-button">Create account <Plus size={15}/></button>
          </form>
          <section className="form-card">
            <span className="card-kicker">WORKSPACE MEMBERS ({accounts.length})</span>
            <h2>Current accounts</h2>
            <ul className="account-list">
              {accounts.map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.username}</strong> <small>({item.role} · {item.active ? 'active' : 'revoked'})</small>
                    {item.active && item.id !== user.id && (
                      <div className="account-role-form">
                        <select aria-label={'Role for ' + item.username} value={roleEdits[item.id] || item.role} onChange={(e) => setRoleEdits((old) => ({ ...old, [item.id]: e.target.value as Role }))}>
                          <option value="archivist">Archivist</option>
                          <option value="researcher">Researcher</option>
                          <option value="operator">Operator</option>
                          <option value="admin">Administrator</option>
                        </select>
                        <button type="button" className="secondary-button" disabled={!roleEdits[item.id] || roleEdits[item.id] === item.role} onClick={() => changeRole(item.id)}>Save role</button>
                      </div>
                    )}
                  </div>
                  {item.active && item.id !== user.id && (
                    <button type="button" className="text-button danger-text" onClick={() => revokeUser(item.id)}>Revoke</button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      {audit.length > 0 && (
        <div className="preview-panel">
          <span className="card-kicker">RECENT AUDIT ACTIVITY</span>
          <div className="table-wrap">
            <table>
              <thead><tr><th>ACTION</th><th>TARGET</th><th>RESULT</th><th>TIME</th></tr></thead>
              <tbody>
                {audit.slice(0, 20).map((row) => (
                  <tr key={row.id}>
                    <td>{row.action}</td>
                    <td>{row.target_type} {row.target_id ? row.target_id.slice(0, 8) : ''}</td>
                    <td><span className="state-chip">{row.result}</span></td>
                    <td>{dateTimeText(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function AssetDetail({
  asset,
  role,
  onClose,
  onUpdated,
  showNotice,
}: {
  asset: Asset;
  role: Role;
  onClose: () => void;
  onUpdated: () => void;
  showNotice: (msg: string) => void;
}) {
  const [detail, setDetail] = useState<any>(null);
  const [insights, setInsights] = useState<AssetInsights | null>(null);
  const [frames, setFrames] = useState<{ id: string; timestamp_ms: number }[]>([]);
  const [activeTimestampMs, setActiveTimestampMs] = useState<number>(asset.matched_timestamp_ms ?? 0);
  const [label, setLabel] = useState('');
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cardNotice, setCardNotice] = useState('');
  const videoRef = useRef<HTMLVideoElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const seekToTimestamp = useCallback((ms: number, autoPlay = true) => {
    setActiveTimestampMs(ms);
    setFlipped(false);
    const el = videoRef.current;
    if (el) {
      try {
        el.currentTime = Math.max(0, ms / 1000);
        if (autoPlay) {
          el.play().catch(() => {});
        }
      } catch {
        // ignore if metadata not yet loaded
      }
    }
  }, []);

  const loadInsights = useCallback(() => {
    return api<AssetInsights>('/api/assets/' + asset.id + '/insights').then(setInsights).catch((e) => setError(e.message));
  }, [asset.id]);

  useEffect(() => {
    setDetail(null); setInsights(null); setFlipped(false); setError(''); setCardNotice('');
    setActiveTimestampMs(asset.matched_timestamp_ms ?? 0);
    api<any>('/api/assets/' + asset.id).then(setDetail).catch((e) => setError(e.message));
    loadInsights();
    if (asset.media_type === 'video') {
      api<{ id: string; timestamp_ms: number }[]>('/api/assets/' + asset.id + '/frames').then(setFrames).catch(() => setFrames([]));
      return;
    }
    setFrames([]);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const timer = window.setTimeout(() => setFlipped(true), reduced ? 0 : 650);
    return () => window.clearTimeout(timer);
  }, [asset.id, asset.media_type, asset.matched_timestamp_ms, loadInsights]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCloseRef.current(); };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
      previous?.focus();
    };
  }, []);

  useEffect(() => { closeButton.current?.focus(); }, [flipped]);

  async function addLabel(e: FormEvent) {
    e.preventDefault(); setError(''); setCardNotice('');
    try {
      await api('/api/assets/' + asset.id + '/annotations', { method: 'POST', body: JSON.stringify({ label }) });
      setLabel('');
      setCardNotice('Human annotation saved.');
      setDetail(await api<any>('/api/assets/' + asset.id));
      onUpdated();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function sendToReview() {
    setBusy(true); setError(''); setCardNotice('');
    try {
      const result = await api<{ review: ReviewState; created: number }>('/api/assets/' + asset.id + '/review', { method: 'POST', body: '{}' });
      const msg = result.created ? `${result.created} AI suggestions sent to the review queue.` : 'Marked for review.';
      setCardNotice(msg);
      showNotice(msg);
      await loadInsights();
      setDetail(await api<any>('/api/assets/' + asset.id));
      onUpdated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const src = insights?.source;
  const review = insights?.review;
  const hasMatchedFrame = asset.media_type === 'video' && asset.matched_timestamp_ms !== null && asset.matched_timestamp_ms !== undefined;
  const media = asset.media_type === 'image'
    ? <img src={'/api/assets/' + asset.id + '/media'} alt={'Full preview of ' + asset.original_filename}/>
    : (
      <video
        ref={videoRef}
        controls
        preload="metadata"
        src={'/api/assets/' + asset.id + '/media'}
        aria-label="Authorized video preview"
        onLoadedMetadata={(e) => {
          if (asset.matched_timestamp_ms !== null && asset.matched_timestamp_ms !== undefined) {
            e.currentTarget.currentTime = Math.max(0, asset.matched_timestamp_ms / 1000);
          }
        }}
      />
    );

  const sourceRows: [string, React.ReactNode][] = src ? [
    ['ORIGINAL FILE', src.original_filename],
    ...(src.source_path ? [['PATH IN SOURCE', src.source_path] as [string, React.ReactNode]] : []),
    ['IMPORTED ON', dateTimeText(src.imported_at)],
    ['FILE TYPE', src.mime],
    ['FILE SIZE', formatSize(src.size_bytes)],
    ...(src.width ? [['DIMENSIONS', src.width + ' × ' + src.height + ' px'] as [string, React.ReactNode]] : []),
    ...(src.duration_ms ? [['DURATION', formatTimestamp(src.duration_ms)] as [string, React.ReactNode]] : []),
  ] : [];

  return (
    <div className="card-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={'flip-card' + (flipped ? ' flipped' : '')} role="dialog" aria-modal="true" aria-label={'Media record: ' + asset.original_filename}>
        <div className="flip-inner">
          <section className="flip-face flip-front" aria-hidden={flipped}>
            <div className="card-topbar">
              <div><span className="card-kicker">{asset.media_type === 'video' ? 'VIDEO RECORD' : 'MEDIA RECORD'}</span><h2>{asset.original_filename}</h2></div>
              <button ref={flipped ? undefined : closeButton} className="icon-button" aria-label="Close media card" onClick={onClose} tabIndex={flipped ? -1 : 0}><X size={18}/></button>
            </div>
            <div className="card-media">
              {media}
              {asset.media_type === 'video' && (
                <span className="player-time-badge">{formatTimestamp(activeTimestampMs)}</span>
              )}
            </div>
            {asset.media_type === 'video' && (
              <div className="video-inspector-bar">
                <div className="video-inspector-main">
                  <div>
                    <div className="video-inspector-meta">
                      Video · {dateText(asset.created_at)}
                      {asset.duration_ms ? ` · ${formatTimestamp(asset.duration_ms)}` : ''}
                      {` · ${formatSize(asset.size_bytes)}`}
                    </div>
                    <div className="video-match-callout">
                      <ScanSearch size={15}/>
                      <span>
                        {hasMatchedFrame && activeTimestampMs === asset.matched_timestamp_ms
                          ? `Matches the frame at ${formatTimestamp(asset.matched_timestamp_ms)}`
                          : `Selected frame at ${formatTimestamp(activeTimestampMs)}`}
                      </span>
                    </div>
                  </div>
                  <div className="video-inspector-buttons">
                    <button
                      type="button"
                      className="primary-button"
                      onClick={() => seekToTimestamp(activeTimestampMs, true)}
                      tabIndex={flipped ? -1 : 0}
                    >
                      Open at {formatTimestamp(activeTimestampMs)}
                    </button>
                    {hasMatchedFrame && activeTimestampMs !== asset.matched_timestamp_ms && (
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => seekToTimestamp(asset.matched_timestamp_ms!, true)}
                        tabIndex={flipped ? -1 : 0}
                      >
                        Jump to match ({formatTimestamp(asset.matched_timestamp_ms)})
                      </button>
                    )}
                  </div>
                </div>
                {frames.length > 0 && (
                  <div className="video-frame-strip" aria-label="Sampled video frames">
                    {frames.map((frame) => {
                      const isMatched = asset.matched_frame_id === frame.id || asset.matched_timestamp_ms === frame.timestamp_ms;
                      const isActive = activeTimestampMs === frame.timestamp_ms;
                      return (
                        <button
                          key={frame.id}
                          type="button"
                          className={`frame-thumb-btn ${isActive ? 'active' : ''} ${isMatched ? 'matched' : ''}`}
                          onClick={() => seekToTimestamp(frame.timestamp_ms, true)}
                          tabIndex={flipped ? -1 : 0}
                          title={`Open at ${formatTimestamp(frame.timestamp_ms)}`}
                        >
                          <img src={`/api/assets/${asset.id}/frames/${frame.id}`} alt={`Frame at ${formatTimestamp(frame.timestamp_ms)}`} loading="lazy"/>
                          <span className="frame-time-label">{formatTimestamp(frame.timestamp_ms)}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
            <div className="card-actions">
              <span className="muted">
                {asset.detected_mime} · {formatSize(asset.size_bytes)}
                {asset.media_type === 'video' && frames.length > 0 ? ` · ${frames.length} indexed frames` : ''}
              </span>
              <button className="primary-button" onClick={() => setFlipped(true)} tabIndex={flipped ? -1 : 0}>Show metadata &amp; AI insights ↻</button>
            </div>
          </section>

          <section className="flip-face flip-back" aria-hidden={!flipped}>
            <div className="card-topbar">
              <div className="card-title">
                {asset.has_thumbnail && <img src={'/api/assets/' + asset.id + '/thumbnail'} alt=""/>}
                <div><span className="card-kicker">MEDIA RECORD · METADATA</span><h2>{asset.original_filename}</h2></div>
              </div>
              <button ref={flipped ? closeButton : undefined} className="icon-button" aria-label="Close media card" onClick={onClose} tabIndex={flipped ? 0 : -1}><X size={18}/></button>
            </div>
            <div className="card-scroll">
              {error && <div className="alert" role="alert">{error}</div>}
              {!insights ? (
                <p className="muted" style={{ padding: '18px 0' }}>Loading metadata and AI insights…</p>
              ) : (
                <>
                  <section className="card-section">
                    <h3>Source &amp; original file</h3>
                    <dl className="metadata">
                      {sourceRows.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
                    </dl>
                  </section>

                  {asset.media_type === 'image' && (
                    <section className="card-section">
                      <h3>Camera metadata</h3>
                      {insights.camera.fields.length ? (
                        <dl className="metadata">
                          {insights.camera.fields.map((field) => (
                            <div key={field.label}><dt>{field.label.toUpperCase()}</dt><dd>{field.value}</dd></div>
                          ))}
                        </dl>
                      ) : (
                        <p className="muted">No camera (EXIF) metadata embedded in the original file.</p>
                      )}
                      {insights.camera.gps_present && <p className="muted">The file contains GPS location data; it is not displayed.</p>}
                    </section>
                  )}

                  <section className="card-section">
                    <h3>Objects detected <span className="ai-badge">AI · unverified</span></h3>
                    <InsightChips result={insights.objects}/>
                  </section>

                  <section className="card-section">
                    <h3>Employees detected</h3>
                    {insights.employees.items.length ? (
                      <div className="chip-list">
                        {insights.employees.items.map((item) => <span className="insight-chip" key={item.label}>{item.label}</span>)}
                      </div>
                    ) : (
                      <p className="muted disabled-note">{insights.employees.message}</p>
                    )}
                  </section>

                  <section className="card-section">
                    <h3>Relevant IIHS keywords <span className="ai-badge">AI · unverified</span></h3>
                    <InsightChips result={insights.keywords}/>
                    {insights.keywords.status === 'ready' && (
                      <p className="muted small-note">Ranked from {insights.keywords.vocabulary_size} institutional keywords. City, state and other place names are excluded.</p>
                    )}
                  </section>

                  {frames.length > 0 && (
                    <section className="card-section frame-list">
                      <h3>Sampled frames ({frames.length}) — click any frame to open video at that timestamp</h3>
                      <div className="video-frame-strip">
                        {frames.map((frame) => {
                          const isMatched = asset.matched_frame_id === frame.id || asset.matched_timestamp_ms === frame.timestamp_ms;
                          return (
                            <button
                              key={frame.id}
                              type="button"
                              className={`frame-thumb-btn ${isMatched ? 'matched' : ''}`}
                              onClick={() => seekToTimestamp(frame.timestamp_ms, true)}
                              tabIndex={flipped ? 0 : -1}
                              title={`Open at ${formatTimestamp(frame.timestamp_ms)}`}
                            >
                              <img
                                src={`/api/assets/${asset.id}/frames/${frame.id}`}
                                alt={`Video frame at ${formatTimestamp(frame.timestamp_ms)}`}
                                loading="lazy"
                              />
                              <span className="frame-time-label">{formatTimestamp(frame.timestamp_ms)}</span>
                            </button>
                          );
                        })}
                      </div>
                    </section>
                  )}

                  <section className="card-section annotations">
                    <h3>Tags on this record</h3>
                    {detail?.annotations?.length ? (
                      detail.annotations.map((a: Asset['annotations'][number]) => (
                        <div className="annotation-row" key={a.id}>
                          <span>{a.label}</span>
                          <span className={'tag ' + a.status}>{a.status} · {a.source}</span>
                        </div>
                      ))
                    ) : (
                      <p className="muted">No tags yet.</p>
                    )}
                    {['admin', 'archivist'].includes(role) && (
                      <form className="inline-form" onSubmit={addLabel}>
                        <label>Add a tag<input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={160} required/></label>
                        <button className="secondary-button">Add tag</button>
                      </form>
                    )}
                  </section>
                </>
              )}
            </div>
            <div className="card-actions">
              <button className="secondary-button" onClick={() => setFlipped(false)} tabIndex={flipped ? 0 : -1}>↻ Show {asset.media_type}</button>
              <a className="secondary-button" href={'/api/assets/' + asset.id + '/media'} download={asset.original_filename} tabIndex={flipped ? 0 : -1}>Download</a>
              <span className="review-status" role="status">
                {cardNotice || (review ? (review.status === 'pending' ? `In review · ${review.pending_suggestions} suggestions pending` : review.status === 'reviewed' ? 'Review complete' : 'Marked for review') : '')}
              </span>
              <button className="primary-button" disabled={busy || !insights || review?.status === 'pending'} onClick={sendToReview} tabIndex={flipped ? 0 : -1}>
                {busy ? 'Sending…' : review?.status === 'pending' ? 'In review' : 'Send to review'}
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function InsightChips({ result }: { result: InsightResult }) {
  if (result.status === 'unavailable') return <p className="muted">The local AI model is unavailable right now. Start the model bridge and reopen this card.</p>;
  if (result.status === 'not_indexed') return <p className="muted">This record has no visual index yet.</p>;
  if (!result.items.length) return <p className="muted">No suggestions.</p>;
  return (
    <div className="chip-list">
      {result.items.map((item) => (
        <span className="insight-chip" key={item.label} title={[item.category, item.notes].filter(Boolean).join(' — ')}>
          {item.label}
          {item.category && <small>{item.category}</small>}
          <b>{Math.round(Math.max(0, item.score) * 100)}%</b>
        </span>
      ))}
    </div>
  );
}

function Toggle({ title, description, checked, onChange, badge }: { title: string; description: string; checked: boolean; onChange: (value: boolean) => void; badge?: string }) {
  return (
    <label className="toggle-row">
      <span className="toggle-copy">
        <strong>{title} {badge && <small>{badge}</small>}</strong>
        <span>{description}</span>
      </span>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}/>
      <span className="toggle-track"><span/></span>
    </label>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
