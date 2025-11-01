<template>
  <div style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial, sans-serif; padding: 16px; max-width: 1200px; margin: 0 auto;">
    <h2>Excel Agent — Vue + FastAPI</h2>

    <section style="margin-bottom: 16px;">
      <h3>Загрузка файлов (CSV/XLSX)</h3>
      <input type="file" multiple @change="onFiles" />
      <div v-if="uploading">Загрузка...</div>
    </section>

    <section style="display: grid; grid-template-columns: 1fr 2fr; gap: 16px; align-items: start;">
      <div>
        <h3>Таблицы</h3>
        <select v-model="selectedTable" @change="loadSample" style="width: 100%;">
          <option v-for="t in tables" :key="t" :value="t">{{ t }}</option>
        </select>
        <div style="margin-top: 8px; display: flex; gap: 8px;">
          <button :disabled="!selectedTable" @click="doExportCsv">Скачать CSV</button>
          <button :disabled="!selectedTable" @click="refreshVersions">История версий</button>
        </div>

        <div v-if="versions.length" style="margin-top: 12px;">
          <strong>Версии:</strong>
          <ul>
            <li v-for="v in versions" :key="v.version_num">
              v{{ v.version_num }} — {{ v.created_at }} — {{ v.operation }}
              <button @click="restore(v.version_num)">Откатиться</button>
              <button @click="exportVersion(v.version_num)">Скачать</button>
            </li>
          </ul>
        </div>
      </div>

      <div>
        <h3>Превью таблицы</h3>
        <table v-if="sample.length" border="1" cellspacing="0" cellpadding="4">
          <thead>
            <tr>
              <th v-for="c in columns" :key="c">{{ c }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in sample" :key="i">
              <td v-for="c in columns" :key="c">{{ r[c] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section style="margin-top: 16px;">
      <h3>График</h3>
      <div style="display: flex; gap: 8px; flex-wrap: wrap;">
        <select v-model="plot.type">
          <option value="bar">Bar</option>
          <option value="line">Line</option>
          <option value="hist">Histogram</option>
          <option value="scatter">Scatter</option>
        </select>
        <input v-model="plot.x" placeholder="x" />
        <input v-model="plot.y" placeholder="y (для bar/line/scatter)" />
        <select v-model="plot.agg">
          <option value="count">count</option>
          <option value="sum">sum</option>
          <option value="mean">mean</option>
          <option value="min">min</option>
          <option value="max">max</option>
        </select>
        <input v-model.number="plot.bins" type="number" min="1" placeholder="bins (hist)" />
        <input v-model="plot.where" placeholder="WHERE (SQL)" style="min-width: 240px;" />
        <button :disabled="!selectedTable" @click="draw">Построить</button>
      </div>
      <div v-if="plotImg" style="margin-top: 12px;">
        <img :src="`data:image/png;base64,${plotImg}`" alt="plot" />
      </div>
    </section>

    <section style="margin-top: 16px;">
      <h3>Запросы (НЛ/SQL)</h3>
      <div style="display: flex; gap: 8px; margin-bottom: 8px;">
        <button :class="{active: queryMode==='nl'}" @click="queryMode='nl'">Естественный язык</button>
        <button :class="{active: queryMode==='sql'}" @click="queryMode='sql'">Чистый SQL</button>
      </div>

      <div v-if="queryMode==='nl'">
        <textarea v-model="nlTask" rows="3" style="width: 100%;" placeholder="Например: посчитай средние продажи по категориям за последний месяц"></textarea>
        <div style="display: flex; gap: 8px; margin-top: 8px;">
          <button :disabled="!nlTask.trim()" @click="genSql">Сгенерировать SQL</button>
          <button :disabled="!nlTask.trim()" @click="runNl">Выполнить</button>
        </div>
        <div v-if="nlGeneratedSql" style="margin-top: 8px;">
          <strong>SQL:</strong>
          <pre style="white-space: pre-wrap;">{{ nlGeneratedSql }}</pre>
        </div>
        <div v-if="nlAnswer" style="margin-top: 8px;">
          <strong>Ответ:</strong>
          <div v-html="nlAnswer"></div>
        </div>
        <div v-if="nlRows.length" style="margin-top: 8px;">
          <table border="1" cellspacing="0" cellpadding="4">
            <thead>
              <tr>
                <th v-for="c in Object.keys(nlRows[0])" :key="c">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in nlRows" :key="i">
                <td v-for="c in Object.keys(nlRows[0])" :key="c">{{ r[c] }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-else>
        <textarea v-model="sqlText" rows="4" style="width: 100%;" placeholder="SELECT ..."></textarea>
        <div style="display: flex; gap: 8px; margin-top: 8px;">
          <button :disabled="!sqlText.trim()" @click="runSql">Выполнить SQL</button>
        </div>
        <div v-if="sqlRows.length" style="margin-top: 8px;">
          <table border="1" cellspacing="0" cellpadding="4">
            <thead>
              <tr>
                <th v-for="c in Object.keys(sqlRows[0])" :key="c">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in sqlRows" :key="i">
                <td v-for="c in Object.keys(sqlRows[0])" :key="c">{{ r[c] }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { uploadFile, listTables, sampleTable, renderPlot, listVersions, restoreVersion, exportCsv, exportCsvVersion, nl2sql, nlquery, sqlQuery } from './api'

const tables = ref<string[]>([])
const selectedTable = ref<string>('')
const uploading = ref(false)
const sample = ref<any[]>([])
const columns = computed(() => (sample.value[0] ? Object.keys(sample.value[0]) : []))
const versions = ref<any[]>([])
const plot = ref<any>({ type: 'bar', x: '', y: '', agg: 'count', bins: 20, where: '' })
const plotImg = ref<string>('')

// NL/SQL queries
const queryMode = ref<'nl' | 'sql'>('nl')
const nlTask = ref('')
const nlGeneratedSql = ref('')
const nlRows = ref<any[]>([])
const nlAnswer = ref<string>('')
const sqlText = ref('SELECT 1')
const sqlRows = ref<any[]>([])

async function refreshTables() {
  const data = await listTables()
  tables.value = data.tables || []
  if (!selectedTable.value && tables.value.length) {
    selectedTable.value = tables.value[0]
    await loadSample()
  }
}

async function onFiles(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  uploading.value = true
  try {
    for (const f of Array.from(files)) {
      await uploadFile(f)
    }
    await refreshTables()
  } finally {
    uploading.value = false
  }
}

async function loadSample() {
  if (!selectedTable.value) return
  const data = await sampleTable(selectedTable.value, 50)
  sample.value = data.rows || []
}

async function draw() {
  if (!selectedTable.value) return
  const payload = { ...plot.value, table: selectedTable.value }
  const data = await renderPlot(payload)
  plotImg.value = data.image_base64
}

async function refreshVersions() {
  if (!selectedTable.value) return
  const data = await listVersions(selectedTable.value)
  versions.value = data.versions || []
}

async function restore(v: number) {
  if (!selectedTable.value) return
  await restoreVersion(selectedTable.value, v)
  await loadSample()
}

function doExportCsv() {
  if (!selectedTable.value) return
  exportCsv(selectedTable.value)
}

function exportVersion(v: number) {
  if (!selectedTable.value) return
  exportCsvVersion(selectedTable.value, v)
}

onMounted(refreshTables)

async function genSql() {
  if (!nlTask.value.trim()) return
  const res = await nl2sql(nlTask.value, selectedTable.value)
  nlGeneratedSql.value = res.sql || ''
}

async function runNl() {
  if (!nlTask.value.trim()) return
  const res = await nlquery(nlTask.value, selectedTable.value)
  nlGeneratedSql.value = res.sql || ''
  nlRows.value = res.rows || []
  nlAnswer.value = res.answer || ''
}

async function runSql() {
  if (!sqlText.value.trim()) return
  const res = await sqlQuery(sqlText.value)
  sqlRows.value = res.rows || []
}
</script>

<style scoped>
button {
  padding: 6px 10px;
}
select, input {
  padding: 6px 8px;
}
table {
  width: 100%;
}
</style>


