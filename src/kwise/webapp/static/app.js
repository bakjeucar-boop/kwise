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
      <div v-else-if="blk.type === 'metrics'" class="row q-gutter-sm">
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
        <div class="q-pa-sm column q-gutter-sm"><k-blocks :blocks="blk.children" /></div>
      </q-expansion-item>
      <q-card v-else-if="blk.type === 'card'" flat class="k-card q-pa-md column q-gutter-xs">
        <k-blocks :blocks="blk.children" />
      </q-card>
      <div v-else-if="blk.type === 'group'" :class="['column q-gutter-sm', blk.dim ? 'k-stale' : '']">
        <k-blocks :blocks="blk.children" />
      </div>
      <div v-else-if="blk.type === 'columns'" class="row no-wrap q-gutter-md">
        <div v-for="(col, j) in blk.columns" :key="j" class="col column items-center">
          <k-blocks :blocks="col" />
        </div>
      </div>
    </template>`,
};

/* ------------------------------------------------------------------ 앱 */

const App = {
  components: { KBlocks, KInfo },
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
    };
  },
  computed: {
    dirty() {
      return this.confirmed !== null && JSON.stringify(this.formPayload()) !== JSON.stringify(this.confirmed);
    },
    schoolNotice() { return this.building.use === "education" ? this.opts.school_notice : ""; },
    hoursLabel() { return `운영 시간대 ${this.building.hours.min}–${this.building.hours.max}시`; },
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
      <div class="q-pa-md column q-gutter-sm">
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
            <q-tab-panel name="s1" class="q-pa-none column q-gutter-md">
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
              <div class="row items-center q-gutter-sm" style="min-height: 1.5rem">
                <q-spinner v-if="status.spin" size="sm" />
                <span :class="status.error ? 'text-negative' : 'k-note'">{{ status.text }}</span>
                <q-btn v-if="overridden" flat no-caps label="열 지정 되돌리기" @click="analyse({ reset: true })" />
              </div>

              <q-expansion-item v-if="columns" dense header-class="k-note" :label="'열 인식 결과 — ' + columns.describe">
                <div class="q-pa-sm column q-gutter-sm">
                  <div class="row q-col-gutter-md">
                    <div class="col-6"><q-select v-model="columns.date" :options="columns.names" label="검침일 열" dense @update:model-value="pickColumns" /></div>
                    <div class="col-6"><q-select v-model="columns.energy" :options="columns.names" label="전력량 열" dense @update:model-value="pickColumns" /></div>
                  </div>
                  <div class="k-note">자동 판정이 빗나갔으면 여기서 고칩니다.</div>
                  <k-blocks :blocks="columns.candidates" />
                </div>
              </q-expansion-item>

              <div :class="['column q-gutter-md', busy ? 'k-stale' : '', dirty ? 'k-dim' : '']">
                <k-blocks :blocks="stage1" />
              </div>
              <div v-if="stage1.length" class="row justify-end items-center q-gutter-md q-mt-lg">
                <span v-if="!stage1Ready" class="k-note">계약 정보를 확정하면 넘어갈 수 있습니다.</span>
                <q-btn unelevated no-caps color="primary" label="다음 단계 — 개선 수단  →"
                  :disable="!stage1Ready" @click="go('s2')" />
              </div>
            </q-tab-panel>

            <!-- ② 개선 수단 -->
            <q-tab-panel name="s2" class="q-pa-none column q-gutter-md">
              <div class="k-h1">② 개선 수단</div>
              <div class="k-note">「① 진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다.</div>
            </q-tab-panel>

            <!-- ③ 개선안 조합 -->
            <q-tab-panel name="s3" class="q-pa-none column q-gutter-md">
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
