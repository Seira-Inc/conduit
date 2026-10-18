const $ = id => document.getElementById(id);
let notes = [], editId = null, mode = 'pen', down = false, start = null, before = null;
const canvas = document.querySelector('canvas'), ctx = canvas.getContext('2d', {willReadFrequently: true});
function renderNotes() {
  $('notes').replaceChildren();
  for (const note of notes) {
    const card = document.createElement('article'); card.className = 'note';
    const title = document.createElement('h3'); title.textContent = note.title;
    const body = document.createElement('p'); body.textContent = note.text;
    const edit = document.createElement('button'); edit.textContent = 'Edit'; edit.setAttribute('aria-label', 'Edit ' + note.title);
    edit.onclick = () => { editId = note.id; $('note-title').value = note.title; $('note-body').value = note.text; $('save-note').textContent = 'Save changes'; $('cancel-edit').hidden = false; };
    const remove = document.createElement('button'); remove.textContent = 'Delete'; remove.setAttribute('aria-label', 'Delete ' + note.title);
    remove.onclick = () => { notes = notes.filter(n => n.id !== note.id); if (editId === note.id) reset(); renderNotes(); };
    card.append(title, body, edit, remove); $('notes').append(card);
  }
  $('note-count').textContent = `${notes.length} note${notes.length === 1 ? '' : 's'}`;
}
function reset() { editId = null; $('note-form').reset(); $('save-note').textContent = 'Create note'; $('cancel-edit').hidden = true; }
$('note-form').onsubmit = event => {
  event.preventDefault(); const item = {id: editId || crypto.randomUUID(), title: $('note-title').value, text: $('note-body').value};
  if (editId) notes = notes.map(n => n.id === editId ? item : n); else notes.push(item);
  reset(); renderNotes();
};
$('cancel-edit').onclick = reset;
function selectMode(value) { mode = value; for (const id of ['pen', 'eraser', 'rectangle']) $(id).setAttribute('aria-pressed', id === value); }
for (const id of ['pen', 'eraser', 'rectangle']) $(id).onclick = () => selectMode(id);
document.addEventListener('keydown', event => { if (['INPUT', 'TEXTAREA'].includes(event.target.tagName)) return; if (event.key.toLowerCase() === 'b') selectMode('pen'); if (event.key.toLowerCase() === 'e') selectMode('eraser'); if (event.key.toLowerCase() === 'r') selectMode('rectangle'); });
function point(event) { const b = canvas.getBoundingClientRect(); return {x: (event.clientX - b.left) * canvas.width / b.width, y: (event.clientY - b.top) * canvas.height / b.height}; }
function ink() { const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data; let count = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) count++; $('ink').textContent = `${count} painted pixels`; }
canvas.addEventListener('pointerdown', event => { canvas.focus(); down = true; start = point(event); before = ctx.getImageData(0, 0, canvas.width, canvas.height); ctx.beginPath(); ctx.moveTo(start.x, start.y); canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener('pointermove', event => {
  if (!down) return; const p = point(event); ctx.strokeStyle = '#205c42'; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.lineWidth = mode === 'eraser' ? 30 : 5;
  ctx.globalCompositeOperation = mode === 'eraser' ? 'destination-out' : 'source-over';
  if (mode === 'rectangle') { ctx.putImageData(before, 0, 0); ctx.strokeRect(start.x, start.y, p.x - start.x, p.y - start.y); }
  else { ctx.lineTo(p.x, p.y); ctx.stroke(); }
});
canvas.addEventListener('pointerup', () => { down = false; ctx.closePath(); ink(); });
$('clear').onclick = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); ink(); };
