const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

export async function uploadFile(file: File, tableName?: string) {
  const form = new FormData();
  form.append('file', file);
  if (tableName) form.append('table_name', tableName);
  form.append('if_exists', 'replace');
  const res = await fetch(`${BASE_URL}/v1/ingest/upload`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listTables() {
  const res = await fetch(`${BASE_URL}/v1/schema/tables`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function sampleTable(table: string, limit = 50) {
  const res = await fetch(`${BASE_URL}/v1/schema/tables/${encodeURIComponent(table)}/sample?limit=${limit}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function renderPlot(payload: any) {
  const res = await fetch(`${BASE_URL}/v1/plots/render`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listVersions(table: string) {
  const res = await fetch(`${BASE_URL}/v1/versioning/versions/${encodeURIComponent(table)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function restoreVersion(table: string, version: number) {
  const res = await fetch(`${BASE_URL}/v1/versioning/versions/${encodeURIComponent(table)}/restore/${version}`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function exportCsv(table: string) {
  window.location.href = `${BASE_URL}/v1/ingest/export/csv?table=${encodeURIComponent(table)}`;
}

export async function exportCsvVersion(table: string, version: number) {
  window.location.href = `${BASE_URL}/v1/versioning/versions/${encodeURIComponent(table)}/${version}/export/csv`;
}

export async function nl2sql(task: string, table_hint?: string) {
  const res = await fetch(`${BASE_URL}/v1/llm/nl2sql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, table_hint })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function nlquery(task: string, table_hint?: string) {
  const res = await fetch(`${BASE_URL}/v1/llm/nlquery`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, table_hint })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function sqlQuery(sql: string, params?: Record<string, any>) {
  const res = await fetch(`${BASE_URL}/v1/query/sql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql, params })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function agentQuery(task: string, table_hint?: string) {
  try {
    const res = await fetch(`${BASE_URL}/v1/llm/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, table_hint })
    });
    if (!res.ok) {
      const errorText = await res.text();
      let errorMessage = errorText;
      try {
        const errorJson = JSON.parse(errorText);
        errorMessage = errorJson.detail || errorJson.message || errorText;
      } catch {
        // Если не JSON, используем текст как есть
      }
      throw new Error(errorMessage || `HTTP ${res.status}: ${res.statusText}`);
    }
    return res.json();
  } catch (error: any) {
    if (error.message) {
      throw error;
    }
    throw new Error(`Ошибка сети: ${error.message || 'Не удалось подключиться к серверу'}`);
  }
}


