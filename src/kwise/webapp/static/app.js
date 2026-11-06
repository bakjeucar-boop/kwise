/* kWise 데스크톱 화면 — 웹서버 없이 pywebview 다리(window.pywebview.api)로 파이썬을 부른다.
 *
 * 결과는 파이썬이 적은 「화면 조각」 목록(blocks)이고, <k-blocks> 하나가 그것을 그린다.
 * 입력(옆단 · 계약 정보 · 카드 입력)만 여기서 칸으로 만든다.
 */
"use strict";

const api = () => window.pywebview.api;

/* 굵게(**) · 줄바꿈만 그리는 작은 마크다운 — 파이썬 쪽 글은 그 둘만 쓴다. */
function md(text) {
  const escaped = String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped
    .replace(/\\([~*_`])/g, "$1")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n\n/g, "<br><br>")
    .replace(/\n/g, "<br>");
}

/* ⓘ — 마우스를 올리면 풀이, Tab 으로 닿아 Enter 로도 연다. */
const KInfo = {
  props: { text: String },
  template: `
    <q-btn flat round dense size="xs" class="k-info" aria-label="풀이" label="ⓘ">
      <q-tooltip class="k-tip" anchor="center right" self="center left" :offset="[8, 0]">
        <div v-html="html"></div>
      </q-tooltip>
      <q-menu><div class="k-tip q-pa-md" v-html="html"></div></q-menu>
    </q-btn>`,
  computed: { html() { return md(this.text); } },
};

/* 그림 하나 — 넣어 둔 vega-embed 로 그린다. 사양이 바뀌면 다시 그린다. */
const KChart = {
  props: { spec: Object },
  template: `<div class="k-chart"></div>`,
  mounted() { this.draw(); },
  watch: { spec() { this.draw(); } },
  methods: {
    draw() {
      // Vue 가 사양을 반응형 대리자(Proxy)로 감싼다 — Vega 가 structuredClone 하지 못하므로
      // 순수 사본을 건넨다.
      const plain = JSON.parse(JSON.stringify(this.spec));
      vegaEmbed(this.$el, plain, { actions: false, renderer: "svg" })
        .catch((e) => console.error("vega", e));
    },
  },
};

const KChartChoices = {
  props: { block: Object },
  components: { KChart },
  data() { return { picked: 0 }; },
  template: `
    <div>
      <q-btn-toggle v-if="block.options.length > 1" v-model="picked" no-caps unelevated rounded dense
        toggle-color="primary" class="q-mb-sm"
        :options="block.options.map((o, i) => ({ label: o.label, value: i }))" />
      <k-chart :spec="block.options[picked].spec" />
      <div v-if="block.caption" class="k-note">{{ block.caption }}</div>
    </div>`,
};

const KTable = {
  props: { block: Object },
  template: `
    <q-table flat dense bordered :rows="block.rows" :columns="block.columns" row-key="__i"
      :pagination="{ rowsPerPage: 0 }" hide-bottom :class="block.compact ? '' : 'full-width'"
      :style="block.compact ? 'max-width: 40rem' : ''">
      <template v-slot:header-cell="props">
        <q-th :props="props">{{ props.col.label }}
          <q-tooltip v-if="props.col.help">{{ props.col.help }}</q-tooltip>
          <span v-if="props.col.help"> ⓘ</span>
        </q-th>
      </template>
    </q-table>`,
};

/* 화면 조각 목록을 그린다 — 파이썬 ``kwise.webapp.blocks`` 와 한 짝이다. */
const KBlocks = {
  name: "k-blocks",
  props: { blocks: { type: Array, default: () => [] } },
  components: { KInfo, KChart, KChartChoices, KTable },
  methods: { md },
  template: `
    <template v-for="(blk, i) in blocks" :key="i">
      <div v-if="blk.type === 'h1'" class="k-h1">{{ blk.text }}</div>
      <div v-else-if="blk.type === 'h2'" class="k-h2">{{ blk.text }}</div>
      <div v-else-if="blk.type === 'h3'" class="k-h3">{{ blk.text }}</div>
      <div v-else-if="blk.type === 'md'" v-html="md(blk.text)"></div>
      <div v-else-if="blk.type === 'caption'" class="row items-center no-wrap">
        <span class="k-note">{{ blk.text }}</span><k-info v-if="blk.help" :text="blk.help" />
      </div>
      <div v-else-if="blk.type === 'caution'" class="k-caution" v-html="'⚠ ' + md(blk.text)"></div>
      <div v-else-if="blk.type === 'blocked'" class="k-blocked" v-html="'⛔ ' + md(blk.text)"></div>
      <div v-else-if="blk.type === 'note'" class="k-note" v-html="'· ' + md(blk.text)"></div>
      <div v-else-if="blk.type === 'overview'" class="k-overview" v-html="md(blk.text)"></div>
      <div v-else-if="blk.type === 'metrics'" class="k-metrics">
        <q-card v-for="(m, j) in blk.items" :key="j" flat class="k-card k-metric">
          <div class="row items-center no-wrap">
            <span class="k-label">{{ m.label }}</span><k-info v-if="m.help" :text="m.help" />
          </div>
          <div class="k-value">{{ m.value }}</div>
          <div v-if="m.sub" class="k-sub">{{ m.sub }}</div>
        </q-card>
      </div>
      <k-chart v-else-if="blk.type === 'chart'" :spec="blk.spec" />
      <k-chart-choices v-else-if="blk.type === 'chart_choices'" :block="blk" />
      <k-table v-else-if="blk.type === 'table'" :block="blk" />
      <q-expansion-item v-else-if="blk.type === 'expansion'" :label="blk.title"
        :default-opened="blk.opened" dense class="full-width">
        <div class="q-pa-sm k-stack-sm"><k-blocks :blocks="blk.children" /></div>
      </q-expansion-item>
      <q-card v-else-if="blk.type === 'card'" flat class="k-card q-pa-md k-stack-sm">
        <k-blocks :blocks="blk.children" />
      </q-card>
      <div v-else-if="blk.type === 'group'" :class="['k-stack-sm', blk.dim ? 'k-stale' : '']">
        <k-blocks :blocks="blk.children" />
      </div>
      <div v-else-if="blk.type === 'columns'" class="k-cols">
        <div v-for="(col, j) in blk.columns" :key="j" class="k-stack-sm items-center">
          <k-blocks :blocks="col" @action="$emit('action', $event)" />
        </div>
      </div>
      <!-- 잉여 처리 고르기 — 태양광 카드 결과 안에 선다. 고르면 카드가 다시 계산한다. -->
      <q-expansion-item v-else-if="blk.type === 'surplus_picker'" label="잉여 처리" default-opened dense
        class="full-width">
        <div class="q-pa-sm k-stack-sm">
          <div class="k-label">남는 전기를 어떻게 하나</div>
          <q-option-group inline :model-value="blk.value" :options="blk.options.map((o) => ({ label: o, value: o }))"
            @update:model-value="(v) => $emit('action', { surplus_use: v })" />
          <q-input v-if="blk.price_field" type="number" dense :label="blk.price_label" :model-value="blk.price"
            style="max-width: 20rem"
            @change="(v) => $emit('action', { [blk.price_field]: v === '' ? 0 : Number(v) })">
            <q-tooltip anchor="center right" self="center left">{{ blk.price_tip }}</q-tooltip>
          </q-input>
        </div>
      </q-expansion-item>
    </template>`,
  emits: ["action"],
};

/* 2단계 카드 하나 — 입력칸은 여기, 결과는 파이썬이 적은 조각. 다시 계산하는 동안 옛 결과를 흐려 둔다. */
const KCard = {
  props: { meta: Object },
  emits: ["rediagnosed"],
  components: { KBlocks, KInfo },
  data() {
    return {
      on: false, loading: false, blocks: [], extra: {}, error: "",
      pf: { target: null, investment: 0 },
      dr: { priced: false, price: 0, off_days: [] },
      ess: { known: false, total: 0 },
      solar: { area: 1000, density: "", azimuth: "", unit_cost: 0, capacity: 0, azimuth_override: 0,
               loss: 0.14, total_known: false, total: 0, wall_area: 0, wall_azimuth: "" },
      sopts: null,
    };
  },
  methods: {
    async toggled(on) {
      await api().toggle(this.meta.key, on);
      if (on) await this.run();
    },
    async run(params = {}) {
      this.loading = true;
      this.error = "";
      try {
        const r = await api().card(this.meta.key, params);
        if (!r.ok) { this.error = r.error; return; }
        this.blocks = r.blocks;
        this.extra = r;
        if (r.solar) this.syncSolar(r.solar);
        if (r.floor !== undefined && this.pf.target === null) this.pf.target = r.target;
        if (r.rediagnosed) this.$emit("rediagnosed");
      } finally { this.loading = false; }
    },
    syncSolar(o) {
      this.sopts = o;
      if (!this.solar.density) this.solar.density = o.default_density;
      if (!this.solar.azimuth) this.solar.azimuth = o.default_azimuth;
      if (!this.solar.wall_azimuth) this.solar.wall_azimuth = o.default_azimuth;
    },
    async solarEdited() {
      // 칸을 고치면 라벨(환산 용량)과 묵은 결과 여부만 다시 — 계산은 「태양광 계산」 단추가 한다.
      this.sopts = await api().solar_options({ ...this.solar });
    },
    action(a) { this.run(a); },
    refresh() { if (this.on) this.run(); },
    restore(state, on) {
      // 작업 열기 — 파이썬 쪽 고른 것은 이미 갈렸다. 입력칸만 맞추고 켠 카드는 다시 계산.
      const box = { power_factor: "pf", demand_response: "dr", ess: "ess", solar: "solar" }[this.meta.key];
      if (box && state) Object.assign(this[box], state);
      this.on = on;
      this.blocks = [];
      this.sopts = null;
      this.refresh();
    },
  },
  template: `
    <div class="k-stack-sm">
      <q-checkbox v-model="on" :label="meta.title" class="text-subtitle1 text-weight-bold"
        @update:model-value="toggled" />
      <div v-if="on" class="q-pl-lg k-stack-sm">
        <!-- 4 역률 -->
        <div v-if="meta.key === 'power_factor'" class="row q-col-gutter-md">
          <div class="col-6"><q-input v-model.number="pf.target" type="number" dense label="목표 지상역률 (%)"
            :min="extra.floor" :max="100" step="0.1" @change="run({ target: pf.target })" /></div>
          <div class="col-6"><q-input v-model.number="pf.investment" type="number" dense label="역률 개선 투자비 (원)"
            @change="run({ investment: pf.investment })" /></div>
        </div>
        <!-- 3 경제성DR -->
        <div v-if="meta.key === 'demand_response'" class="k-stack-sm">
          <div class="k-line">
            <q-checkbox v-model="dr.priced" label="정산 단가를 안다 (사업자 제시값)"
              @update:model-value="run({ priced: dr.priced, price: dr.price })" />
            <q-input v-if="dr.priced" v-model.number="dr.price" type="number" dense label="정산 단가 (원/kWh)"
              style="width: 12rem" @change="run({ price: dr.price })" />
          </div>
          <q-select v-if="(extra.off_day_options || []).length" v-model="dr.off_days" multiple use-chips dense
            emit-value map-options :options="extra.off_day_options" label="쉬는 날이어서 뺄 날짜"
            @update:model-value="run({ off_days: dr.off_days })">
            <q-tooltip anchor="center right" self="center left">근로자의 날이나 임시공휴일이 섞여 있을 수 있습니다. 고른 날은 거래 가능일에서 빼고 감축량을 다시 계산합니다.</q-tooltip>
          </q-select>
        </div>
        <!-- 6 ESS -->
        <div v-if="meta.key === 'ess'" class="k-line">
          <q-checkbox v-model="ess.known" label="견적 총액을 안다" @update:model-value="run({ known: ess.known, total: ess.total })" />
          <q-input v-if="ess.known" v-model.number="ess.total" type="number" dense label="견적 총액 (원)"
            style="width: 14rem" @change="run({ total: ess.total })" />
        </div>
        <!-- 5 태양광 -->
        <div v-if="meta.key === 'solar' && sopts" class="k-stack-sm">
          <div class="row q-col-gutter-md items-start">
            <div class="col-5"><q-input v-model.number="solar.area" type="number" dense label="설치 가능 면적 (m²)"
              @change="solarEdited" /></div>
            <div class="col-7">
              <div class="row items-center"><span class="k-label">{{ sopts.density_label }}</span><k-info :text="sopts.density_tip" /></div>
              <q-option-group v-model="solar.density" inline dense :options="sopts.densities" @update:model-value="solarEdited" />
            </div>
          </div>
          <div class="k-note">지역 — {{ sopts.region.replace('/', ' ') }} (옆단 「건물 정보」 에서 고칩니다)</div>
          <div class="k-label">방위</div>
          <q-option-group v-model="solar.azimuth" inline dense :options="sopts.azimuths" @update:model-value="solarEdited" />
          <div v-if="!sopts.ran" class="k-note">· 방위별 상대 발전량은 「태양광 계산」 을 누른 뒤 라벨에 붙습니다.</div>
          <div v-if="sopts.northward[solar.azimuth]" class="k-caution"><b>⚠ {{ sopts.northward[solar.azimuth] }}</b></div>
          <q-input v-model.number="solar.unit_cost" type="number" dense label="설치 단가 (원/kWp)" style="max-width: 16rem"
            @change="solarEdited">
            <q-tooltip anchor="center right" self="center left">견적 단가입니다. 넣지 않으면 투자비와 회수기간을 산출하지 않습니다.</q-tooltip>
          </q-input>
          <q-expansion-item label="상세" dense>
            <div class="q-pa-sm row q-col-gutter-md">
              <div class="col-6"><q-input v-model.number="solar.capacity" type="number" dense label="설치 용량 직접 입력 (kWp) — 0 이면 면적 환산" @change="solarEdited" /></div>
              <div class="col-6"><q-input v-model.number="solar.loss" type="number" step="0.01" dense label="시스템 손실" @change="solarEdited" /></div>
              <div class="col-6"><q-input v-model.number="solar.azimuth_override" type="number" dense label="방위각 직접 입력 (도) — 0 이면 위 8방위 선택" @change="solarEdited" /></div>
              <div class="col-6 column">
                <q-checkbox v-model="solar.total_known" label="총 투자비를 안다" @update:model-value="solarEdited" />
                <q-input v-if="solar.total_known" v-model.number="solar.total" type="number" dense label="총 투자비 (원)" @change="solarEdited" />
              </div>
              <div class="col-12"><b>벽면 어레이</b> — 지붕과 방위를 따로 고릅니다. 0 이면 지붕 한 벌입니다.</div>
              <div class="col-6"><q-input v-model.number="solar.wall_area" type="number" dense label="벽면 설치 가능 면적 (m²)" @change="solarEdited" /></div>
              <div class="col-12" v-if="solar.wall_area > 0">
                <div class="k-label">벽면 방위</div>
                <q-option-group v-model="solar.wall_azimuth" inline dense :options="sopts.wall_azimuths" @update:model-value="solarEdited" />
              </div>
            </div>
          </q-expansion-item>
          <div><q-btn unelevated no-caps color="primary" label="태양광 계산" :loading="loading"
            @click="run({ form: { ...solar }, calculate: true })" /></div>
          <div v-if="sopts.stale" class="k-caution"><b>⚠ 입력이 변경되었습니다 — 다시 계산하십시오. 아래는 이전 결과입니다.</b></div>
        </div>

        <div v-if="error" class="k-blocked">⛔ {{ error }}</div>
        <div v-if="loading && !blocks.length" class="k-line">
          <q-spinner size="sm" /><span class="k-note">계산하는 중…</span>
        </div>
        <div :class="['k-stack-sm', loading ? 'k-stale' : '', (sopts && sopts.stale) ? 'k-dim' : '']">
          <k-blocks :blocks="blocks" @action="action" />
        </div>
      </div>
    </div>`,
};

/* 「파일 → 기준 데이터」 — 줄마다 값 · 저장 · 확인함 · 되돌리기, 원복 둘, ESS 단가. 고친 것이 서면 saved. */
const KRules = {
  components: { KBlocks, KInfo },
  emits: ["saved", "ess"],
  data() { return { page: null, edits: {}, preview: null, busy: false, failed: [] }; },
  async mounted() { this.show(await api().rules_page()); },
  methods: {
    show(page) {
      this.page = page;
      this.edits = Object.fromEntries(page.rows.map((r) => [r.key, r.value]));
    },
    async act(action, key, row = null) {
      this.busy = true;
      try {
        const r = await api().rule_act(action, key, row ? row.kind : "", row ? this.edits[key] : null);
        this.failed = r.blocks || [];
        if (r.page) this.show(r.page);
        if (r.ok) { this.preview = null; this.$q.notify({ type: "positive", message: `✓ ${r.message}` }); this.$emit("saved"); }
      } finally { this.busy = false; }
    },
    async ask(origin) { this.preview = await api().rules_preview(origin); },
    async essChanged() {
      const e = this.page.ess;
      this.page.ess = await api().set_ess_pricing(e.path, e.fixed, e.per_kwh);
      this.$emit("ess");
    },
  },
  template: `
    <div v-if="!page" class="k-line"><q-spinner size="sm" /><span class="k-note">읽는 중…</span></div>
    <div v-else class="k-stack">
      <k-blocks :blocks="page.head" />
      <div class="k-card q-pa-md k-stack-sm">
        <div><b>ESS 투자비 단가</b> — {{ page.ess.formula }}</div>
        <div class="k-note">{{ page.ess.provenance }}</div>
        <div class="k-label">단가 경로</div>
        <q-option-group v-model="page.ess.path" inline dense :options="page.ess.paths.map((p) => ({ label: p, value: p }))"
          @update:model-value="essChanged" />
        <div class="k-cols">
          <q-input v-model.number="page.ess.fixed" type="number" dense step="1000000" min="0"
            label="고정비 (원) — PCS·PMS·컨테이너·소방·공조·UPS·변압기" @change="essChanged" />
          <q-input v-model.number="page.ess.per_kwh" type="number" dense step="10000" min="0"
            label="용량단가 (원/kWh) — 배터리" @change="essChanged" />
        </div>
        <k-blocks :blocks="page.ess.detail" />
      </div>
      <div class="k-h2">원복</div>
      <div class="k-note">직전 상태 · 출고 상태 · 항목별 셋을 모두 씁니다. 항목별 되돌리기는 각 줄에 있습니다.</div>
      <div v-for="o in page.origins" :key="o.value" class="k-line">
        <span style="flex: 1"><b>{{ o.label }}</b> ({{ o.filename }})</span>
        <q-btn flat dense no-caps label="직전 상태로" :disable="busy" @click="act('previous', o.value)" />
        <q-btn flat dense no-caps label="출고 상태로" :disable="busy" @click="ask(o.value)" />
      </div>
      <div v-if="preview" class="k-stack-sm">
        <k-blocks :blocks="preview.blocks" />
        <div class="k-line">
          <q-btn unelevated no-caps color="primary" label="확인했습니다 — 기본값으로 되돌립니다" :disable="busy"
            @click="act('defaults', preview.origin)" />
          <q-btn flat no-caps label="취소" @click="preview = null" />
        </div>
      </div>
      <k-blocks v-if="failed.length" :blocks="failed" />
      <q-separator />
      <div v-for="row in page.rows" :key="row.key" class="k-card q-pa-md">
        <div class="k-cols">
          <div class="k-stack-sm">
            <div><b>{{ row.badge }}</b> · {{ row.label }}<br><code>{{ row.key }}</code></div>
            <div v-if="row.flags" class="k-note">{{ row.flags }}</div>
            <div class="k-note">{{ row.source }} <k-info :text="row.tip" /></div>
            <div class="k-note">{{ row.verified }}</div>
            <div v-if="row.note" class="k-note">{{ row.note }}</div>
          </div>
          <div class="k-stack-sm">
            <q-checkbox v-if="row.kind === 'bool'" v-model="edits[row.key]" label="값" />
            <q-input v-else-if="row.kind === 'int' || row.kind === 'float'" v-model.number="edits[row.key]"
              type="number" dense label="값" :step="row.kind === 'int' ? 1 : 'any'" />
            <q-input v-else-if="row.kind === 'str'" v-model="edits[row.key]" dense label="값" />
            <template v-else>
              <code style="white-space: pre-wrap">{{ row.value }}</code>
              <div class="k-note">구조가 있는 값은 tools\\export_rules_xlsx.py 로 고칩니다.</div>
            </template>
            <div class="k-line">
              <q-btn v-if="row.kind !== 'other'" unelevated dense no-caps color="primary" label="저장" :disable="busy"
                @click="act('set', row.key, row)" />
              <q-btn flat dense no-caps label="확인함" :disable="busy" @click="act('confirm', row.key)">
                <q-tooltip>값은 그대로 두고 확인일만 갱신합니다.</q-tooltip></q-btn>
              <q-btn v-if="row.changed" flat dense no-caps label="되돌리기" :disable="busy" @click="act('item', row.key)">
                <q-tooltip>기본값 {{ row.default }} 로 되돌립니다.</q-tooltip></q-btn>
            </div>
            <div v-if="row.changed" class="k-note">기본값 — {{ row.default }}</div>
          </div>
        </div>
      </div>
      <q-separator />
      <k-blocks :blocks="page.tail" />
    </div>`,
};

/* ------------------------------------------------------------------ 앱 */

const App = {
  components: { KBlocks, KInfo, KCard, KRules },
  data() {
    return {
      ready: false,
      tab: "s1",
      opts: { types: [], uses: [], provinces: [], lagging_default: 92, hours_default: [9, 18] },
      // 옆단 — 건물 정보
      building: { name: "", use: "", province: "", sigungu: "", hours: { min: 9, max: 18 }, area: null, year: null },
      sigungu: [],
      // 계약 정보 (넷 + 역률 둘)
      contract: { type: "", contract_kw: null, voltage: "", option: "", lagging: 92, leading: null },
      types: [], voltages: [], tariffs: [], leadingApplies: true,
      confirmed: null,          // 「계약 정보 확정」 을 누른 값 (계산에 들어간 것)
      filename: "",
      busy: false,
      again: false,             // 도는 중에 또 부르면 끝난 뒤 한 번 더
      status: { text: "", error: false, spin: false },
      over: false,
      overridden: false,
      tentative: [],
      columns: null,
      stage1: [],
      stage1Ready: false,
      s2: null,                 // 2단계 머리 (기준선 · 대표일 · 카드 목록)
      cardRefs: {},
      s3: null, s3loading: false, combining: false,
      dlTab: "excel", timeseries: true, making: false, made: {}, makeError: "",
      restoring: false,         // 작업 열기 중 — 종별 · 전압을 바꿔도 기본값으로 되돌리지 않는다
      pendingCards: null,       // 작업 열기 — 2단계가 서면 카드 입력칸에 넣을 값
      workPath: "",
      rulesOpen: false,
      alerts: [],
    };
  },
  computed: {
    dirty() {
      return this.confirmed !== null && JSON.stringify(this.formPayload()) !== JSON.stringify(this.confirmed);
    },
    schoolNotice() { return this.building.use === "education" ? this.opts.school_notice : ""; },
    hoursLabel() { return `운영 시간대 ${this.building.hours.min}–${this.building.hours.max}시`; },
    tiers() {
      // 투자 0원 · 저투자 · 투자 — 묶음이 절이고 카드가 그 안의 항목이다.
      const groups = [];
      for (const c of (this.s2?.cards || [])) {
        if (!groups.length || groups[groups.length - 1].tier !== c.tier) groups.push({ tier: c.tier, cards: [] });
        groups[groups.length - 1].cards.push(c);
      }
      return groups;
    },
  },
  async mounted() {
    const o = await api().options();
    this.opts = o;
    this.contract.lagging = o.lagging_default;
    this.building.hours = { min: o.hours_default[0], max: o.hours_default[1] };
    this.types = o.types;
    this.contract.type = o.types[0].value;
    await this.refreshVoltages();
    this.alerts = await api().alerts();
    this.ready = true;
    await api().ready({ ready: true, types: this.types.length });
  },
  watch: {
    "contract.type"() { if (!this.restoring) this.refreshVoltages(); },
    "contract.voltage"() { if (!this.restoring) this.refreshTariffs(); },
    // 3단계는 탭을 열 때 짓는다 — 켠 수단을 다시 모으는 데 몇 초 걸린다.
    tab(value) {
      if (value === "s3") this.openStage3();
      if (value === "s2") this.$nextTick(() => this.applyPending());
    },
  },
  methods: {
    md,
    async refreshVoltages() {
      if (!this.contract.type) return;
      this.voltages = await api().voltages(this.contract.type);
      this.contract.voltage = this.voltages.length ? this.voltages[0].value : "";
      await this.refreshTariffs();
    },
    async refreshTariffs() {
      if (!this.contract.voltage) return;
      const r = await api().tariff_options(this.contract.type, this.contract.voltage);
      this.tariffs = r.options;
      this.leadingApplies = r.leading;
      if (!this.tariffs.some((t) => t.value === this.contract.option)) {
        this.contract.option = this.tariffs.length ? this.tariffs[0].value : "";
      }
    },
    async useChanged() {
      this.types = await api().contract_types(this.building.use);
      if (!this.types.some((t) => t.value === this.contract.type)) this.contract.type = this.types[0].value;
      await this.analyse();
    },
    async provinceChanged() {
      this.sigungu = await api().sigungu(this.building.province);
      this.building.sigungu = this.sigungu.length ? this.sigungu[0].value : "";
      await this.analyse();
    },
    buildingPayload() {
      const bd = this.building;
      return { name: bd.name, use: bd.use, province: bd.province, sigungu: bd.sigungu,
               hours: [bd.hours.min, bd.hours.max], area: bd.area, year: bd.year };
    },
    formPayload() {
      const c = this.contract;
      return { type: c.type, voltage: c.voltage, option: c.option, contract_kw: c.contract_kw,
               lagging: c.lagging, leading: this.leadingApplies ? c.leading : null };
    },
    say(text, { error = false, spin = false } = {}) { this.status = { text, error, spin }; },
    async confirm() {
      this.confirmed = this.formPayload();
      await this.analyse();
    },
    async analyse(columns = null) {
      if (this.busy) { this.again = true; return; }
      this.busy = true;
      if (this.filename) this.say(`${this.filename} — 분석하는 중…`, { spin: true });
      try {
        const r = await api().analyse({ building: this.buildingPayload(), form: this.confirmed, columns });
        this.tentative = r.tentative || [];
        if (r.stage === "no_file") {
          this.say(this.confirmed ? "계약 정보를 확정했습니다. 파일을 올리면 금액이 나옵니다." : "");
        } else if (!r.ok) {
          this.say(r.error, { error: true });
          this.overridden = !!r.overridden;
        } else {
          this.columns = r.columns;
          this.stage1 = r.blocks;
          this.stage1Ready = r.ready;
          this.overridden = false;
          this.say(r.filename);
          await this.loadStage2();
        }
      } finally {
        this.busy = false;
        if (this.again) { this.again = false; await this.analyse(); }
      }
    },
    pickFile() { this.$refs.file.click(); },
    async fileChosen(ev) {
      const file = ev.target.files?.[0] || ev.dataTransfer?.files?.[0];
      this.over = false;
      if (!file) return;
      const buffer = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buffer);
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      await api().upload(file.name, btoa(binary));
      this.filename = file.name;
      if (this.$refs.file) this.$refs.file.value = "";
      await this.analyse();
    },
    pickColumns() { this.analyse({ date: this.columns.date, energy: this.columns.energy }); },
    go(tab) { this.tab = tab; window.scrollTo({ top: 0 }); },
    async loadStage2() {
      // 1단계가 다시 돌면 켜 둔 카드도 새 결과로 다시 — 카드의 입력칸 값은 그대로 둔다.
      this.s2 = await api().stage2();
      if (!this.s2.ready) return;
      await this.$nextTick();
      for (const [key, ref] of Object.entries(this.cardRefs)) {
        if (ref && !(this.pendingCards && key in this.pendingCards)) ref.refresh();
      }
      this.applyPending();
    },
    applyPending() {
      // 2단계 탭은 처음 열 때 선다 — 아직 안 선 카드는 값을 들고 있다가 그때 넣는다.
      if (!this.pendingCards || !this.s2?.ready) return;
      for (const [key, state] of Object.entries(this.pendingCards)) {
        const ref = this.cardRefs[key];
        if (!ref) continue;
        ref.restore(state, this.s2.enabled.includes(key));
        delete this.pendingCards[key];
      }
    },
    screenPayload() {
      return { building: this.buildingPayload(), contract: { ...this.contract }, confirmed: this.confirmed };
    },
    async saveWork() {
      const r = await api().save_work(this.screenPayload());
      if (r.ok) { this.workPath = r.path; this.$q.notify({ type: "positive", message: `작업을 저장했습니다 — ${r.path}` }); }
      else if (!r.cancelled) this.$q.notify({ type: "negative", message: r.error });
    },
    async openWork() {
      const r = await api().open_work();
      if (r.cancelled) return;
      if (!r.ok) { this.$q.notify({ type: "negative", message: r.error }); return; }
      await this.restore(r);
    },
    async restore(r) {
      // 화면 값을 파일 값으로 — 종별 → 전압 → 선택요금 차례로 목록을 받아 가며 넣는다.
      const sc = r.screen || {};
      this.restoring = true;
      try {
        const bd = sc.building || {};
        const hours = bd.hours || this.opts.hours_default;
        Object.assign(this.building, { ...bd, hours: { min: hours[0], max: hours[1] } });
        this.types = await api().contract_types(this.building.use || "");
        this.sigungu = await api().sigungu(this.building.province || "");
        const c = sc.contract || {};
        if (c.type) {
          this.contract.type = c.type;
          this.voltages = await api().voltages(c.type);
          this.contract.voltage = c.voltage;
          const t = await api().tariff_options(c.type, c.voltage);
          this.tariffs = t.options;
          this.leadingApplies = t.leading;
          Object.assign(this.contract, c);
        }
        this.confirmed = sc.confirmed ?? null;
        await this.$nextTick();
      } finally { this.restoring = false; }
      this.filename = r.filename;
      this.workPath = r.path;
      this.stage1 = []; this.stage1Ready = false; this.columns = null;
      this.s2 = null; this.s3 = null; this.made = {};
      this.pendingCards = r.cards;
      this.tab = "s1";
      await this.analyse();
    },
    async saveResult() {
      const r = await api().save_result();
      if (r.ok) this.$q.notify({ type: "positive", message: `결과를 저장했습니다 — ${r.path}`,
        actions: [{ label: "열어보기", color: "white", handler: () => this.openPath(r.path) }] });
      else if (!r.cancelled) this.$q.notify({ type: "negative", message: r.error });
    },
    async dayChanged() {
      await api().set_day(this.s2.day, this.s2.day === "custom" ? this.s2.custom : "");
      for (const key of ["power_factor", "solar", "ess"]) this.cardRefs[key]?.refresh();
    },
    async openStage3() {
      this.s3loading = true;
      this.made = {};
      this.makeError = "";
      try { this.s3 = await api().stage3(); } finally { this.s3loading = false; }
    },
    async combine(compute) {
      this.combining = true;
      try {
        const r = await api().combine({ ...this.s3.checked }, compute);
        if (r.ok) Object.assign(this.s3, r);
      } finally { this.combining = false; }
    },
    async makeReport(kind) {
      this.making = true;
      this.makeError = "";
      try {
        const r = await api().make_report(kind, this.timeseries);
        if (r.ok) {
          const folder = r.path.replace(/[\\/][^\\/]+$/, "");
          this.made = { ...this.made, [kind]: { ...r, folder } };
        } else this.makeError = r.error;
      } finally { this.making = false; }
    },
    async openPath(path) {
      const r = await api().open_path(path);
      if (!r.ok) this.$q.notify({ type: "negative", message: r.error });
    },
    async rulesSaved() {
      // 기준 데이터가 바뀌었다 — 알림을 새로 받고 1단계부터 다시 (3단계는 다음에 열 때 새로).
      this.alerts = await api().alerts();
      this.s3 = null;
      await this.analyse();
    },
    essPriced() {
      this.s3 = null;
      this.cardRefs.ess?.refresh();
    },
    async rediagnosed() {
      // 경제성DR 쉬는 날 — 파이썬이 1단계부터 다시 돌렸다. 1단계 화면 · 다른 카드도 새로.
      await this.analyse();
    },
  },
  template: `
  <q-layout view="hHh Lpr lff">
    <q-header class="k-bar">
      <q-toolbar class="q-px-sm">
        <q-btn flat no-caps label="파일">
          <q-menu>
            <q-list dense style="min-width: 14rem">
              <q-item clickable v-close-popup @click="openWork"><q-item-section>작업 열기…</q-item-section></q-item>
              <q-item clickable v-close-popup :disable="!filename" @click="saveWork"><q-item-section>작업 저장…</q-item-section></q-item>
              <q-separator />
              <q-item clickable v-close-popup :disable="!stage1Ready" @click="saveResult"><q-item-section>결과 저장 (JSON)…</q-item-section></q-item>
              <q-separator />
              <q-item clickable v-close-popup @click="rulesOpen = true"><q-item-section>기준 데이터…</q-item-section></q-item>
            </q-list>
          </q-menu>
        </q-btn>
        <q-separator vertical inset class="q-mx-sm" />
        <q-tabs v-model="tab" no-caps inline-label class="k-steps" align="left" indicator-color="transparent">
          <q-tab name="s1" label="🩺 진단" />
          <q-tab name="s2" label="🛠️ 개선 수단" />
          <q-tab name="s3" label="🧩 개선안 조합" />
        </q-tabs>
      </q-toolbar>
    </q-header>

    <q-drawer show-if-above side="left" bordered :width="280">
      <div class="q-pa-md k-stack-sm">
        <div class="k-label">건물 정보</div>
        <q-input v-model="building.name" label="건물명 (선택)" placeholder="건물명 미입력" dense
          @change="analyse()">
          <q-tooltip anchor="center right" self="center left">산출물 표지와 본문에 들어갑니다. 넣지 않으면 「건물명 미입력」 으로 나갑니다.</q-tooltip>
        </q-input>
        <q-select v-model="building.use" :options="opts.uses" emit-value map-options label="용도 (선택)" dense
          @update:model-value="useChanged" />
        <q-select v-model="building.province" :options="opts.provinces" emit-value map-options label="시도 (선택)" dense
          @update:model-value="provinceChanged" />
        <q-select v-if="sigungu.length" v-model="building.sigungu" :options="sigungu" emit-value map-options
          label="시군구" dense @update:model-value="analyse()" />
        <div class="k-label q-mt-md">{{ hoursLabel }}</div>
        <q-range v-model="building.hours" :min="0" :max="24" :step="1" markers snap label
          @change="analyse()" />
        <q-input v-model.number="building.area" type="number" label="연면적 (m², 선택)" dense @change="analyse()" />
        <q-input v-model.number="building.year" type="number" label="준공연도 (선택)" dense @change="analyse()" />
      </div>
    </q-drawer>

    <q-page-container>
      <q-page class="q-pa-lg">
        <q-dialog v-model="rulesOpen" maximized transition-show="slide-up" transition-hide="slide-down">
          <q-card class="k-rules">
            <q-bar class="k-bar">
              <div class="text-weight-bold">기준 데이터</div>
              <q-space />
              <q-btn flat dense no-caps label="닫기" v-close-popup />
            </q-bar>
            <q-card-section class="k-page"><k-rules @saved="rulesSaved" @ess="essPriced" /></q-card-section>
          </q-card>
        </q-dialog>
        <div class="k-page">
          <div v-if="alerts.length" class="k-stack-sm q-mb-md"><k-blocks :blocks="alerts" /></div>
          <q-tab-panels v-model="tab" animated keep-alive class="bg-transparent">
            <!-- 진단 -->
            <q-tab-panel name="s1" class="q-pa-none k-stack">
              <div class="k-h1">🩺 진단</div>
              <div class="k-note">파일만 올려도 결과가 나옵니다. 금액은 계약 정보를 확정하면 나옵니다.</div>
              <q-card flat class="k-card q-pa-md">
                <div class="k-label q-mb-sm">계약 정보</div>
                <div class="row q-col-gutter-md">
                  <div class="col-6">
                    <q-select v-model="contract.type" :options="types" emit-value map-options label="계약종별" dense />
                    <div v-if="schoolNotice" class="k-note q-mt-xs">{{ schoolNotice }}</div>
                  </div>
                  <div class="col-6"><q-input v-model.number="contract.contract_kw" type="number" label="계약전력 (kW)" placeholder="청구서 기재값" dense /></div>
                  <div class="col-6"><q-select v-model="contract.voltage" :options="voltages" emit-value map-options label="전압구분" dense /></div>
                  <div class="col-6"><q-select v-model="contract.option" :options="tariffs" emit-value map-options label="선택요금" dense /></div>
                  <div class="col-6">
                    <q-input v-model.number="contract.lagging" type="number" label="주간 지상역률 (%)" dense>
                      <q-tooltip anchor="center right" self="center left">모르면 그대로 두십시오. 이 값에서 조정액이 0원입니다.</q-tooltip>
                    </q-input>
                  </div>
                  <div class="col-6" v-if="leadingApplies">
                    <q-input v-model.number="contract.leading" type="number" label="야간 진상역률 (%)" dense />
                  </div>
                </div>
                <div v-if="dirty" class="k-caution q-mt-sm"><b>⚠ 바꾼 값은 「계약 정보 확정」 을 눌러야 계산에 들어갑니다.</b></div>
                <q-btn class="q-mt-md" unelevated no-caps color="primary" label="계약 정보 확정"
                  :loading="busy && !!filename" @click="confirm" />
              </q-card>
              <k-blocks :blocks="tentative" />

              <div :class="['k-drop', over ? 'k-over' : '']" @click="pickFile"
                @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="fileChosen">
                <q-icon name="upload_file" size="md" color="primary" />
                <div>사용량 파일 (csv · xls · xlsx) — 눌러서 고르거나 여기로 끌어 놓으십시오</div>
                <input ref="file" type="file" accept=".csv,.xls,.xlsx" style="display: none" @change="fileChosen">
              </div>
              <div class="k-line" style="min-height: 1.5rem">
                <q-spinner v-if="status.spin" size="sm" />
                <span :class="status.error ? 'text-negative' : 'k-note'">{{ status.text }}</span>
                <q-btn v-if="overridden" flat no-caps label="열 지정 되돌리기" @click="analyse({ reset: true })" />
              </div>

              <q-expansion-item v-if="columns" dense header-class="k-note" :label="'열 인식 결과 — ' + columns.describe">
                <div class="q-pa-sm k-stack-sm">
                  <div class="row q-col-gutter-md">
                    <div class="col-6"><q-select v-model="columns.date" :options="columns.names" label="검침일 열" dense @update:model-value="pickColumns" /></div>
                    <div class="col-6"><q-select v-model="columns.energy" :options="columns.names" label="전력량 열" dense @update:model-value="pickColumns" /></div>
                  </div>
                  <div class="k-note">자동 판정이 빗나갔으면 여기서 고칩니다.</div>
                  <k-blocks :blocks="columns.candidates" />
                </div>
              </q-expansion-item>

              <div :class="['k-stack', busy ? 'k-stale' : '', dirty ? 'k-dim' : '']">
                <k-blocks :blocks="stage1" />
              </div>
              <div v-if="stage1.length" class="row justify-end items-center q-gutter-md q-mt-lg">
                <span v-if="!stage1Ready" class="k-note">계약 정보를 확정하면 넘어갈 수 있습니다.</span>
                <q-btn unelevated no-caps color="primary" label="다음 단계 — 개선 수단  →"
                  :disable="!stage1Ready" @click="go('s2')" />
              </div>
            </q-tab-panel>

            <!-- 개선 수단 -->
            <q-tab-panel name="s2" class="q-pa-none k-stack">
              <div class="k-h1">🛠️ 개선 수단</div>
              <div v-if="!s2 || !s2.ready" class="k-note">「진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다.</div>
              <template v-else>
                <div class="k-note">{{ s2.baseline }}</div>
                <div v-if="s2.days.length" class="k-line">
                  <q-select v-model="s2.day" :options="s2.days" emit-value map-options dense label="일일 곡선 대표일"
                    style="min-width: 22rem" @update:model-value="dayChanged">
                    <q-tooltip anchor="center right" self="center left">역률·태양광·ESS 의 하루 곡선이 모두 이 날을 씁니다. 기본은 기간 최대수요가 난 날입니다 — 피크가 어떻게 생겼는지가 세 수단의 공통 관심사이기 때문입니다.</q-tooltip>
                  </q-select>
                  <q-input v-if="s2.day === 'custom'" v-model="s2.custom" type="date" dense label="날짜"
                    :min="s2.range[0]" :max="s2.range[1]" @change="dayChanged" />
                </div>
                <template v-for="(group, gi) in tiers" :key="gi">
                  <div class="k-h2">{{ group.tier }}</div>
                  <k-card v-for="c in group.cards" :key="c.key" :meta="c" :ref="(el) => cardRefs[c.key] = el"
                    @rediagnosed="rediagnosed" />
                </template>
                <div class="row justify-end q-mt-lg">
                  <q-btn unelevated no-caps color="primary" label="다음 단계 — 개선안 조합  →" @click="go('s3')" />
                </div>
              </template>
            </q-tab-panel>

            <!-- 개선안 조합 -->
            <q-tab-panel name="s3" class="q-pa-none k-stack">
              <div class="k-h1">🧩 개선안 조합</div>
              <div v-if="s3loading" class="k-line"><q-spinner size="sm" /><span class="k-note">켠 개선안을 모으는 중…</span></div>
              <div v-else-if="!s3 || !s3.ready" class="k-note">{{ s3 && s3.error ? s3.error : "「진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다." }}</div>
              <template v-else>
                <k-blocks :blocks="s3.scope" />
                <k-blocks :blocks="s3.summary" />
                <div class="k-h2">조합 구성</div>
                <div v-if="!s3.reviewed.length" class="k-note">· 2단계에서 개선안을 하나도 켜지 않아 조합할 것이 없습니다.</div>
                <template v-else>
                  <div class="k-line">
                    <q-checkbox v-for="r in s3.reviewed" :key="r.key" v-model="s3.checked[r.key]" :label="r.label"
                      @update:model-value="combine(false)" />
                  </div>
                  <div class="k-note">체크한 개선안만 합산효과에 넣습니다. 체크를 풀어도 위의 개선안별 요약에는 남습니다 — 뺀 것이 얼마짜리였는지 보여야 뺄지 말지 정할 수 있습니다.</div>
                  <div v-for="line in s3.stale" :key="line" class="k-caution"><b>⚠ {{ line }}</b></div>
                  <div><q-btn unelevated no-caps color="primary" label="합산효과 계산" :loading="combining"
                    :disable="s3.stale.length > 0" @click="combine(true)" /></div>
                </template>
                <div :class="combining ? 'k-stale' : ''" class="k-stack"><k-blocks :blocks="s3.combined" /></div>

                <div class="k-h2">내려받기</div>
                <q-tabs v-model="dlTab" no-caps dense align="left" class="text-primary">
                  <q-tab name="excel" label="Excel — 분석자용" />
                  <q-tab name="ppt" label="PPT 보고서 — 의사결정자용" />
                </q-tabs>
                <div v-if="dlTab === 'excel'" class="k-stack-sm">
                  <div class="k-note">부록 셋을 포함한 통합문서입니다. 파일명에 날짜·시각이 붙습니다.</div>
                  <q-checkbox v-model="timeseries" label="시계열 시트 포함" />
                </div>
                <div v-else class="k-stack-sm">
                  <div class="k-note">한 장에 한 가지만 담은 슬라이드입니다. 켠 수단만 한 장씩 들어갑니다.</div>
                  <div class="k-note">표지 이름 — <b>{{ building.name || "건물명 미입력" }}</b> (옆단 「건물 정보」 에서 고칩니다)</div>
                </div>
                <div v-for="line in s3.downloads_blocked" :key="'d' + line" class="k-caution"><b>⚠ {{ line }}</b></div>
                <div><q-btn unelevated no-caps color="primary" :loading="making"
                  :label="dlTab === 'excel' ? 'Excel 만들기' : 'PPT 보고서 만들기'"
                  :disable="s3.downloads_blocked.length > 0" @click="makeReport(dlTab)" /></div>
                <div v-if="made[dlTab]" class="k-line">
                  <span class="k-note">저장했습니다 — {{ made[dlTab].path }} · {{ Math.round(made[dlTab].kilobytes).toLocaleString() }} KB</span>
                  <q-btn unelevated dense no-caps color="primary" label="열어보기" @click="openPath(made[dlTab].path)" />
                  <q-btn flat dense no-caps label="폴더 열기" @click="openPath(made[dlTab].folder)" />
                </div>
                <div v-if="makeError" class="k-blocked">⛔ {{ makeError }}</div>
              </template>
            </q-tab-panel>
          </q-tab-panels>
        </div>
      </q-page>
    </q-page-container>
  </q-layout>`,
};

const app = Vue.createApp(App);
app.use(Quasar, { lang: Quasar.lang.koKR, plugins: { Notify: Quasar.Notify }, config: { dark: "auto" } });
/* pywebview 가 다리를 다 놓은 뒤에 앱을 붙인다 — 그 전에 부르면 api 가 없다. */
window.addEventListener("pywebviewready", () => app.mount("#app"));
