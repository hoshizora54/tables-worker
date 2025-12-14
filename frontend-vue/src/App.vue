<template>
  <div style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial, sans-serif; padding: 16px; max-width: 1200px; margin: 0 auto;">
    <h2>Агент для анализа данных</h2>
    <p style="color: #666; margin-bottom: 24px;">
      Задавайте вопросы на естественном языке. Агент автоматически выберет нужные инструменты для анализа данных.
    </p>

    <section style="margin-bottom: 24px;">
      <h3>Загрузка файлов (CSV/XLSX)</h3>
      <input type="file" multiple @change="onFiles" />
      <div v-if="uploading">Загрузка...</div>
    </section>

    <!-- Отображение загруженной таблицы -->
    <section v-if="sample.length && selectedTable" style="margin-bottom: 24px;">
      <h3>Загруженная таблица: {{ selectedTable }}</h3>
      <div style="overflow-x: auto; border: 1px solid #ddd; border-radius: 4px; background: white;">
        <table border="0" cellspacing="0" cellpadding="8" style="width: 100%; border-collapse: collapse;">
          <thead style="background: #f8f9fa; position: sticky; top: 0;">
            <tr>
              <th v-for="c in columns" :key="c" style="text-align: left; padding: 12px; border-bottom: 2px solid #dee2e6; font-weight: 600;">{{ c }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in sample" :key="i" style="border-bottom: 1px solid #dee2e6;">
              <td v-for="c in columns" :key="c" style="padding: 12px;">{{ r[c] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p style="color: #666; font-size: 12px; margin-top: 8px;">
        Показано {{ sample.length }} строк из таблицы {{ selectedTable }}
      </p>
    </section>

    <section style="margin-bottom: 24px;">
      <h3>Запрос на естественном языке</h3>
      <textarea 
        v-model="agentQueryText" 
        rows="4" 
        style="width: 100%; padding: 12px; font-size: 14px; border: 1px solid #ddd; border-radius: 4px;"
        placeholder="Например: &#10;- Построй гистограмму распределения возраста&#10;- Сравни продажи в группах А и Б с помощью t-теста&#10;- Посчитай средние продажи по категориям&#10;- Создай сводную таблицу по регионам и продуктам"
      ></textarea>
      <div style="display: flex; gap: 8px; margin-top: 8px;">
        <button 
          :disabled="!agentQueryText.trim() || processing" 
          @click="runAgent"
          style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;"
        >
          {{ processing ? 'Обработка...' : 'Выполнить' }}
        </button>
        <button 
          v-if="agentQueryText"
          @click="agentQueryText = ''"
          style="padding: 10px 20px; background: #6c757d; color: white; border: none; border-radius: 4px; cursor: pointer;"
        >
          Очистить
        </button>
      </div>
    </section>

    <section v-if="agentResult" style="margin-top: 24px;">
      <h3>Результат</h3>
      
      <div v-if="agentResult.error" style="margin-bottom: 16px; padding: 12px; background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 4px; color: #721c24;">
        <strong>Ошибка:</strong>
        <div style="margin-top: 8px;">{{ agentResult.error }}</div>
      </div>
      
      <div v-if="agentResult.final_answer" style="margin-bottom: 16px; padding: 12px; background: #f8f9fa; border-radius: 4px;">
        <strong>Ответ:</strong>
        <div style="margin-top: 8px; white-space: pre-wrap;">{{ agentResult.final_answer }}</div>
      </div>

      <div v-if="agentResult.results && agentResult.results.length" style="margin-bottom: 16px;">
        <strong>Выполненные инструменты:</strong>
        <ul style="margin-top: 8px;">
          <li v-for="(result, idx) in agentResult.results" :key="idx" style="margin-bottom: 8px;">
            <strong>{{ result.tool_name }}</strong>
            <span v-if="result.error" style="color: #dc3545;"> — Ошибка: {{ result.error }}</span>
            <span v-else-if="result.result && result.result.image_base64" style="color: #28a745;"> — График построен</span>
            <span v-else-if="result.result && result.result.rows" style="color: #28a745;"> — Получено {{ result.result.count || result.result.rows.length }} строк</span>
            <span v-else style="color: #28a745;"> — Выполнено</span>
          </li>
        </ul>
      </div>

      <!-- График -->
      <div v-if="plotImage" style="margin-bottom: 16px;">
        <h4>График</h4>
        <img :src="`data:image/png;base64,${plotImage}`" alt="plot" style="max-width: 100%; border: 1px solid #ddd; border-radius: 4px;" />
      </div>

      <!-- Таблицы с результатами -->
      <div v-for="(result, idx) in resultTables" :key="idx" style="margin-bottom: 24px;">
        <h4 v-if="result.title">{{ result.title }}</h4>
        <div style="overflow-x: auto;">
          <table border="1" cellspacing="0" cellpadding="8" style="width: 100%; border-collapse: collapse;">
            <thead style="background: #f8f9fa;">
              <tr>
                <th v-for="c in result.columns" :key="c" style="text-align: left; padding: 8px;">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in result.rows" :key="i">
                <td v-for="c in result.columns" :key="c" style="padding: 8px;">{{ r[c] }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Боковая панель с управлением таблицами -->
    <section v-if="tables.length" style="margin-top: 24px; padding: 16px; background: #f8f9fa; border-radius: 4px;">
      <h3>Управление таблицами</h3>
      <div style="display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; margin-bottom: 12px;">
        <select v-model="selectedTable" @change="loadSample" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
          <option v-for="t in tables" :key="t" :value="t">{{ t }}</option>
        </select>
        <div style="display: flex; gap: 8px;">
          <button :disabled="!selectedTable" @click="doExportCsv" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Скачать CSV</button>
          <button :disabled="!selectedTable" @click="refreshVersions" style="padding: 8px 16px; background: #17a2b8; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">История версий</button>
        </div>
      </div>

      <div v-if="versions.length" style="margin-top: 12px;">
        <strong>Версии:</strong>
        <ul style="margin-top: 8px;">
          <li v-for="v in versions" :key="v.version_num" style="margin-bottom: 8px; padding: 8px; background: white; border-radius: 4px;">
            v{{ v.version_num }} — {{ v.created_at }} — {{ v.operation }}
            <div style="margin-top: 4px;">
              <button @click="restore(v.version_num)" style="padding: 4px 8px; margin-right: 4px; background: #ffc107; color: black; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Откатиться</button>
              <button @click="exportVersion(v.version_num)" style="padding: 4px 8px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Скачать</button>
            </div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { uploadFile, listTables, sampleTable, listVersions, restoreVersion, exportCsv, exportCsvVersion, agentQuery } from './api'

const tables = ref<string[]>([])
const selectedTable = ref<string>('')
const uploading = ref(false)
const sample = ref<any[]>([])
const columns = computed(() => (sample.value[0] ? Object.keys(sample.value[0]) : []))
const versions = ref<any[]>([])

// Агентский запрос
const agentQueryText = ref('')
const processing = ref(false)
const agentResult = ref<any>(null)
const plotImage = ref<string>('')
const resultTables = ref<any[]>([])

async function refreshTables() {
  try {
    const data = await listTables()
    tables.value = data.tables || []
    if (!selectedTable.value && tables.value.length) {
      selectedTable.value = tables.value[0]
      await loadSample()
    } else if (selectedTable.value && tables.value.includes(selectedTable.value)) {
      // Обновляем превью текущей таблицы
      await loadSample()
    }
  } catch (error: any) {
    console.error('Ошибка при загрузке таблиц:', error)
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
  try {
    const data = await sampleTable(selectedTable.value, 50)
    sample.value = data.rows || []
    console.log('Загружено строк:', sample.value.length) // Для отладки
  } catch (error: any) {
    console.error('Ошибка при загрузке превью:', error)
    sample.value = []
  }
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

async function runAgent() {
  if (!agentQueryText.value.trim()) return
  processing.value = true
  plotImage.value = ''
  resultTables.value = []
  agentResult.value = null
  
  try {
    const result = await agentQuery(agentQueryText.value, selectedTable.value || undefined)
    agentResult.value = result
    
    console.log('Agent result:', result) // Для отладки
    
    // Извлекаем график
    if (result.results && Array.isArray(result.results)) {
      for (const toolResult of result.results) {
        if (toolResult.result && toolResult.result.image_base64) {
          plotImage.value = toolResult.result.image_base64
          break
        }
      }
    }
    
    // Извлекаем таблицы из результатов инструментов
    resultTables.value = []
    if (result.results && Array.isArray(result.results)) {
      for (const toolResult of result.results) {
        // Проверяем разные возможные структуры ответа
        let rows = null
        
        if (toolResult.result) {
          // Прямой доступ к rows
          if (toolResult.result.rows && Array.isArray(toolResult.result.rows)) {
            rows = toolResult.result.rows
          }
          // Или rows могут быть в другом месте
          else if (toolResult.result.data && Array.isArray(toolResult.result.data)) {
            rows = toolResult.result.data
          }
        }
        
        if (rows && rows.length > 0) {
          const cols = Object.keys(rows[0])
          resultTables.value.push({
            title: `Результат: ${toolResult.tool_name || 'неизвестный инструмент'}`,
            columns: cols,
            rows: rows.slice(0, 100) // Ограничиваем для отображения
          })
        }
      }
    }
    
    // Если нет результатов в tool_results, но есть final_answer, показываем его
    if (resultTables.value.length === 0 && result.final_answer) {
      console.log('Только финальный ответ, нет таблиц')
    }
  } catch (error: any) {
    console.error('Ошибка агента:', error)
    const errorMessage = error.message || error.toString() || 'Ошибка при выполнении запроса'
    agentResult.value = { error: errorMessage }
    // Показываем более подробную информацию об ошибке
    if (errorMessage.includes('Failed to fetch') || errorMessage.includes('Load failed')) {
      agentResult.value.error = 'Не удалось подключиться к серверу. Убедитесь, что бэкенд запущен на http://localhost:8000'
    }
  } finally {
    processing.value = false
  }
}
</script>

<style scoped>
button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>


