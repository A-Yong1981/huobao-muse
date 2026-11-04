// Run inside the Huobao app container after placing the ArcReel project at
// /app/data/legacy-arcreel/project. The source tree is retained untouched.
const fs = require('fs');
const path = require('path');
const Database = require('/app/backend/node_modules/better-sqlite3');

const dataRoot = '/app/data';
const sourceRoot = path.join(dataRoot, 'legacy-arcreel/project');
const project = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'project.json'), 'utf8'));
const db = new Database(path.join(dataRoot, 'huobao.sqlite3'));
db.pragma('busy_timeout = 5000');
const now = new Date().toISOString();
const publicBase = (process.env.HUOBAO_PUBLIC_BASE || 'http://192.168.58.200:5679').replace(/\/$/, '');
const existing = db.prepare("SELECT id FROM dramas WHERE metadata LIKE '%proj-1f7ce179%' AND deleted_at IS NULL").get();
if (existing) throw new Error(`ArcReel project already imported as Huobao drama ${existing.id}`);

function assetPath(sheet) {
  if (!sheet) return null;
  const relative = path.posix.normalize(sheet.replaceAll('\\', '/'));
  if (relative.startsWith('../') || relative.startsWith('/') || !/^(characters|scenes|props)\//.test(relative)) {
    throw new Error(`Invalid asset path: ${sheet}`);
  }
  const from = path.join(sourceRoot, ...relative.split('/'));
  if (!fs.existsSync(from)) throw new Error(`Missing registered asset: ${sheet}`);
  const dest = path.join(dataRoot, 'static/imported/arcreel', ...relative.split('/'));
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(from, dest);
  return `static/imported/arcreel/${relative}`;
}

function rewritePrompt(value) {
  if (!value) return '';
  return String(value).replace(/https?:\/\/(?:are|arcreel)\.bearly\.de5\.net\/api\/v1\/files\/proj-1f7ce179\/(characters|scenes|props)\/([^\s，。；;）)\]"']+)/g, (whole, type, file) => {
    let name;
    try { name = decodeURIComponent(file); } catch { return whole; }
    const p = `static/imported/arcreel/${type}/${name}`;
    return fs.existsSync(path.join(dataRoot, ...p.split('/'))) ? `${publicBase}/${p.split('/').map(encodeURIComponent).join('/')}` : whole;
  });
}

function linesForScript(script) {
  const lines = [`${script.title || ''}`, ''];
  for (const shot of script.scenes || []) {
    lines.push(`${shot.scene_id || ''}  ${(shot.scenes || []).join(' / ')}`);
    if (shot.note) lines.push(shot.note);
    for (const utterance of shot.utterances || []) {
      lines.push(`${utterance.speaker || utterance.kind || '旁白'}：${utterance.text || ''}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

function insert(sql, ...args) { return Number(db.prepare(sql).run(...args).lastInsertRowid); }
const stats = { episodes: 0, storyboards: 0, characters: 0, scenes: 0, props: 0, images: 0, utterances: 0, unresolved: [] };

const importAll = db.transaction(() => {
  const dramaId = insert(`INSERT INTO dramas
    (title,description,genre,style,aspect_ratio,total_episodes,status,metadata,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)`, project.title, typeof project.overview === 'string' ? project.overview : JSON.stringify(project.overview || ''),
    'drama', project.style || '真人电视剧', project.aspect_ratio || '9:16', project.episodes.length, 'draft',
    JSON.stringify({ migrated_from: 'arcreel', source_project_id: 'proj-1f7ce179', backup: '20261103-174407' }), now, now);
  const posterDir = path.join(sourceRoot, 'renders/posters');
  if (fs.existsSync(posterDir)) {
    const poster = fs.readdirSync(posterDir).find(name => /\.(png|jpe?g|webp)$/i.test(name));
    if (poster) {
      const extension = path.extname(poster).toLowerCase();
      const thumbnail = `static/imported/arcreel/poster${extension}`;
      fs.mkdirSync(path.join(dataRoot, 'static/imported/arcreel'), { recursive: true });
      fs.copyFileSync(path.join(posterDir, poster), path.join(dataRoot, thumbnail));
      db.prepare('UPDATE dramas SET thumbnail=? WHERE id=?').run(thumbnail, dramaId);
    }
  }

  const characterIds = new Map();
  for (const [name, item] of Object.entries(project.characters || {})) {
    const image = assetPath(item.character_sheet);
    if (image) stats.images++;
    const id = insert(`INSERT INTO characters
      (drama_id,name,role,description,appearance,styling,final_prompt,personality,image_url,sort_order,local_path,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, dramaId, name, '', item.description || '', item.description || '', '',
      item.description || '', '', image, stats.characters, image, now, now);
    characterIds.set(name, id); stats.characters++;
  }
  const sceneIds = new Map();
  for (const [name, item] of Object.entries(project.scenes || {})) {
    const image = assetPath(item.scene_sheet);
    if (image) stats.images++;
    const id = insert(`INSERT INTO scenes
      (drama_id,location,time,prompt,final_prompt,storyboard_count,image_url,status,local_path,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`, dramaId, name, '', item.description || name, item.description || name, 1,
      image, image ? 'completed' : 'pending', image, now, now);
    sceneIds.set(name, id); stats.scenes++;
  }
  const propIds = new Map();
  for (const [name, item] of Object.entries(project.props || {})) {
    const image = assetPath(item.prop_sheet);
    if (image) stats.images++;
    const id = insert(`INSERT INTO props
      (drama_id,name,description,prompt,final_prompt,image_url,local_path,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?)`, dramaId, name, item.description || '', item.description || name,
      item.description || name, image, image, now, now);
    propIds.set(name, id); stats.props++;
  }

  const episodeLink = {
    character: db.prepare('INSERT INTO episode_characters (episode_id,character_id,created_at) VALUES (?,?,?)'),
    scene: db.prepare('INSERT INTO episode_scenes (episode_id,scene_id,created_at) VALUES (?,?,?)'),
    prop: db.prepare('INSERT INTO episode_props (episode_id,prop_id,created_at) VALUES (?,?,?)'),
  };
  const shotCharacter = db.prepare('INSERT INTO storyboard_characters (storyboard_id,character_id) VALUES (?,?)');
  const shotProp = db.prepare('INSERT INTO storyboard_props (storyboard_id,prop_id) VALUES (?,?)');

  for (const episode of project.episodes || []) {
    const n = Number(episode.episode);
    const script = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'scripts', `episode_${n}.json`), 'utf8'));
    const duration = (script.scenes || []).reduce((sum, shot) => sum + (Number(shot.duration_seconds) || 0), 0);
    const episodeId = insert(`INSERT INTO episodes
      (drama_id,episode_number,title,content,script_content,description,duration,status,resolution,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`, dramaId, n, script.title || episode.title || `第${n}集`,
      linesForScript(script), linesForScript(script), [script.hook, script.next_episode_teaser].filter(Boolean).join('\n'),
      duration, 'draft', '1080p', now, now);
    stats.episodes++;
    const linked = { character: new Set(), scene: new Set(), prop: new Set() };
    for (const [index, shot] of (script.scenes || []).entries()) {
      const sceneId = sceneIds.get((shot.scenes || [])[0]) || null;
      const dialogue = (shot.utterances || []).map(u => `${u.speaker || u.kind || '旁白'}：${u.text || ''}`);
      stats.utterances += dialogue.length;
      const description = [shot.note || '', dialogue.length ? `对白：\n${dialogue.join('\n')}` : ''].filter(Boolean).join('\n\n');
      const storyboardId = insert(`INSERT INTO storyboards
        (episode_id,scene_id,storyboard_number,title,location,time,image_prompt,video_prompt,description,duration,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, episodeId, sceneId, index + 1, shot.scene_id || `E${n}S${index + 1}`,
        (shot.scenes || []).join(' / '), '', rewritePrompt(shot.image_prompt), rewritePrompt(shot.video_prompt),
        description, Number(shot.duration_seconds) || 0, 'pending', now, now);
      stats.storyboards++;
      for (const name of shot.characters_in_scene || []) {
        const id = characterIds.get(name);
        if (!id) { stats.unresolved.push(`character:${name}`); continue; }
        shotCharacter.run(storyboardId, id);
        if (!linked.character.has(id)) { episodeLink.character.run(episodeId, id, now); linked.character.add(id); }
      }
      for (const name of shot.scenes || []) {
        const id = sceneIds.get(name);
        if (!id) { stats.unresolved.push(`scene:${name}`); continue; }
        if (!linked.scene.has(id)) { episodeLink.scene.run(episodeId, id, now); linked.scene.add(id); }
      }
      for (const name of shot.props || []) {
        const id = propIds.get(name);
        if (!id) { stats.unresolved.push(`prop:${name}`); continue; }
        shotProp.run(storyboardId, id);
        if (!linked.prop.has(id)) { episodeLink.prop.run(episodeId, id, now); linked.prop.add(id); }
      }
    }
  }
  return dramaId;
});

try {
  const dramaId = importAll();
  console.log(JSON.stringify({ drama_id: dramaId, ...stats, unresolved: [...new Set(stats.unresolved)] }));
} finally { db.close(); }
