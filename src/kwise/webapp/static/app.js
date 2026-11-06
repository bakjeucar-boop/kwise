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

/* ------------------------------------------------------------------ 앱 */

const App = {
  components: { KBlocks, KInfo, KCard },
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
    this.ready = true;
    await api().ready({ ready: true, types: this.types.length });
  },
  watch: {
    "contract.type"() { this.refreshVoltages(); },
    "contract.voltage"() { this.refreshTariffs(); },
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
      for (const ref of Object.values(this.cardRefs)) if (ref) ref.refresh();
    },
    async dayChanged() {
      await api().set_day(this.s2.day, this.s2.day === "custom" ? this.s2.custom : "");
      for (const key of ["power_factor", "solar", "ess"]) this.cardRefs[key]?.refresh();
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
              <q-item clickable v-close-popup disable><q-item-section>작업 열기…</q-item-section></q-item>
              <q-item clickable v-close-popup disable><q-item-section>작업 저장…</q-item-section></q-item>
              <q-separator />
              <q-item clickable v-close-popup disable><q-item-section>결과 저장 (JSON)…</q-item-section></q-item>
              <q-separator />
              <q-item clickable v-close-popup disable><q-item-section>기준 데이터…</q-item-section></q-item>
            </q-list>
          </q-menu>
        </q-btn>
        <q-separator vertical inset class="q-mx-sm" />
        <q-tabs v-model="tab" no-caps inline-label class="k-steps" align="left">
          <q-tab name="s1" label="① 진단" />
          <q-tab name="s2" label="② 개선 수단" />
          <q-tab name="s3" label="③ 개선안 조합" />
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
        <div class="k-page">
          <q-tab-panels v-model="tab" animated keep-alive class="bg-transparent">
            <!-- ① 진단 -->
            <q-tab-panel name="s1" class="q-pa-none k-stack">
              <div class="k-h1">① 진단</div>
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

            <!-- ② 개선 수단 -->
            <q-tab-panel name="s2" class="q-pa-none k-stack">
              <div class="k-h1">② 개선 수단</div>
              <div v-if="!s2 || !s2.ready" class="k-note">「① 진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다.</div>
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

            <!-- ③ 개선안 조합 -->
            <q-tab-panel name="s3" class="q-pa-none k-stack">
              <div class="k-h1">③ 개선안 조합</div>
              <div class="k-note">「① 진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다.</div>
            </q-tab-panel>
          </q-tab-panels>
        </div>
      </q-page>
    </q-page-container>
  </q-layout>`,
};

const app = Vue.createApp(App);
app.use(Quasar, { lang: Quasar.lang.koKR, config: { dark: "auto" } });
/* pywebview 가 다리를 다 놓은 뒤에 앱을 붙인다 — 그 전에 부르면 api 가 없다. */
window.addEventListener("pywebviewready", () => app.mount("#app"));
