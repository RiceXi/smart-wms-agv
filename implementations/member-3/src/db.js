import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, openSync, closeSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { dirname } from 'node:path';

export class Store {
  constructor(filename = ':memory:') {
    if (filename !== ':memory:') {
      mkdirSync(dirname(filename), { recursive: true });
      this.lockPath = `${filename}.lock`;
      try { this.lockHandle = openSync(this.lockPath, 'wx'); }
      catch (error) {
        if (error.code !== 'EEXIST') throw error;
        const pid = Number(readFileSync(this.lockPath, 'utf8'));
        let active = true;
        try { process.kill(pid, 0); } catch (failure) { active = failure.code !== 'ESRCH'; }
        if (active) throw Object.assign(new Error(`数据库已被进程 ${pid} 使用，请关闭原服务后重试`), { code: 'WMS_DB_BUSY', ownerPid: pid });
        unlinkSync(this.lockPath);
        this.lockHandle = openSync(this.lockPath, 'wx');
      }
      writeFileSync(this.lockHandle, String(process.pid));
    }
    this.database = new DatabaseSync(filename);
    this.database.exec(`
      PRAGMA journal_mode = DELETE;
      PRAGMA foreign_keys = ON;
      CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY CHECK (id = 1), document TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, tick INTEGER NOT NULL, type TEXT NOT NULL, document TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS events_tick ON events(tick);
      CREATE TABLE IF NOT EXISTS replay (sequence INTEGER PRIMARY KEY AUTOINCREMENT, tick INTEGER NOT NULL, document TEXT NOT NULL);
    `);
  }
  load() { const row = this.database.prepare('SELECT document FROM snapshots WHERE id = 1').get(); return row ? JSON.parse(row.document) : null; }
  save(state, events = [], frame = null) {
    this.database.exec('BEGIN IMMEDIATE');
    try {
      this.database.prepare('INSERT INTO snapshots VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET document = excluded.document').run(JSON.stringify(state));
      const insert = this.database.prepare('INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)');
      for (const event of events) insert.run(event.id, event.tick, event.type, JSON.stringify(event));
      if (frame) this.database.prepare('INSERT INTO replay(tick, document) VALUES (?, ?)').run(frame.tick, JSON.stringify(frame));
      this.database.exec('COMMIT');
    } catch (error) { this.database.exec('ROLLBACK'); throw error; }
  }
  events() { return this.database.prepare('SELECT document FROM events ORDER BY rowid').all().map(row => JSON.parse(row.document)); }
  frames() { return this.database.prepare('SELECT document FROM replay ORDER BY sequence').all().map(row => JSON.parse(row.document)); }
  reset() { this.database.exec('DELETE FROM snapshots; DELETE FROM events; DELETE FROM replay;'); }
  close() {
    this.database.close();
    if (this.lockHandle !== undefined) { closeSync(this.lockHandle); unlinkSync(this.lockPath); this.lockHandle = undefined; }
  }
}
