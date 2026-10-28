<template>
  <!-- The pause menu's BeamNG.Earth card (the Mods tab): the connection, the quality presets at one click, and the
       full settings page (EarthSettings.vue, a route of its own: the user, 2026-10-28) -->
  <div class="earth-card">
    <BngCardHeading>BeamNG.Earth</BngCardHeading>
    <div class="card-status" :class="{ 'is-connected': connected }">{{ connected ? "Connected" : "Not connected" }}</div>

    <div class="card-label">Quality</div>
    <div class="card-presets">
      <BngButton
        v-for="p in presets"
        :key="p.id"
        class="card-preset"
        :accent="quality === p.id ? ACCENTS.main : ACCENTS.outlined"
        @click="setQuality(p.id)"
      >{{ p.name }}</BngButton>
    </div>
    <div class="card-note">{{ quality === "custom" ? "Custom: your own mix (in the full settings)." : "Applies at once." }}</div>

    <BngButton class="card-open" :accent="ACCENTS.main" @click="openSettings">Open BeamNG.Earth settings</BngButton>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue"
import { BngButton, BngCardHeading, ACCENTS } from "@/common/components/base"

const presets = [
  { id: "low", name: "Low" }, { id: "medium", name: "Medium" }, { id: "high", name: "High" }, { id: "ultra", name: "Ultra" },
]
const quality = ref("")
const status = ref({})
let pollTimer = null

const connected = computed(() => status.value.connection === "connected")

function engineLua(script, cb) {
  if (!window.bngApi || !window.bngApi.engineLua) return
  window.bngApi.engineLua(script, cb)
}

function refresh() {
  engineLua("beamngEarth_settings and beamngEarth_settings.uiState() or nil", state => {
    if (!state) return
    if (state.settings) quality.value = state.settings.quality || ""
    if (state.status) status.value = state.status
  })
}

function setQuality(id) {
  quality.value = id
  engineLua(`beamngEarth_settings and beamngEarth_settings.uiSet("quality", ${JSON.stringify(id)}) or nil`, res => {
    if (res && res.settings) quality.value = res.settings.quality
  })
}

function openSettings() {
  engineLua("if beamngEarth_settings and beamngEarth_settings.openSettings then beamngEarth_settings.openSettings() end")
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, 2000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
})
</script>

<style scoped lang="scss">
/* Only spacing: the pause menu's own card around it, the game's buttons and heading */
.earth-card {
  display: flex;
  flex-direction: column;
  gap: 0.6em;
  padding: 0.5em 0.75em 1em;
}

.card-status { font-weight: 600; color: var(--bng-add-red-400, #f88); &.is-connected { color: var(--bng-add-green-400, #8f8); } }

.card-label { color: var(--bng-cool-gray-200, #ccc); }

.card-presets {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.4em;
}

.card-note { font-size: 0.85em; color: var(--bng-cool-gray-300, #bbb); }
</style>
