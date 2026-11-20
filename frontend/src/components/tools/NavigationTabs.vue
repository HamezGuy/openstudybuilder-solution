<template>
  <v-select
    v-if="
      choices.length > 1 &&
      (smAndDown || !appStore.drawer || appStore.sidebarAutoMinimize)
    "
    v-model="tab"
    class="section-picker"
    density="compact"
    variant="outlined"
    :label="$t('Workspace.sections')"
    :items="choices"
    item-title="title"
    item-value="value"
    hide-details
  />
  <v-window v-if="activePanel" :model-value="activePanel" class="bg-white">
    <slot name="default" :tab-keys="tabKeys" :active-panel="activePanel"></slot>
  </v-window>
</template>

<script setup>
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  watchEffect,
} from 'vue'
import { useDisplay } from 'vuetify'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'

const props = defineProps({
  tabs: {
    type: Array,
    default: null,
  },
  breadcrumbsLevel: {
    type: Number,
    default: 3,
  },
  tabKey: {
    type: String,
    default: 'tab',
  },
  tabTitle: {
    type: String,
    default: 'name',
  },
  persist: {
    type: Boolean,
    default: true,
  },
})
const emit = defineEmits(['tabChanged'])

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()

const { smAndDown } = useDisplay()
const owningRoute = route.name
const choices = computed(() =>
  (props.tabs || []).map((item) => ({
    title: item[props.tabTitle],
    value: item[props.tabKey],
    to: {
      name: owningRoute,
      params: { ...route.params, tab: item[props.tabKey] },
      query: route.query,
      hash: route.hash,
    },
  }))
)
watchEffect(() => {
  if (route.name === owningRoute)
    appStore.screenNavigation = { route: owningRoute, choices: choices.value }
})
onBeforeUnmount(() => {
  if (appStore.screenNavigation?.route === owningRoute)
    appStore.screenNavigation = null
})

const tab = ref(null)
const activePanel = ref(null)
const tabKeys = ref({})

const updateTabKey = (value) => {
  if (tabKeys.value[value] === undefined) {
    tabKeys.value[value] = 0
  }
  if (props.persist) {
    return
  }
  tabKeys.value[value]++
}

// Resolve a tab key from whatever's in the URL — falls back to the first
// tab when the URL carries no tab param or an unknown one. Without this,
// a bookmarked URL like /library/activities/bogus would crash the watcher
// at find(...)[props.tabTitle].
const resolveTabKey = (raw) => {
  if (!props.tabs?.length) return null
  if (raw && props.tabs.some((el) => el[props.tabKey] === raw)) return raw
  // Older study views stored the translated label in the URL and breadcrumbs.
  const legacy = props.tabs.find((el) => el[props.tabTitle] === raw)
  if (legacy) return legacy[props.tabKey]
  return props.tabs[0][props.tabKey]
}

function activate(value) {
  if (value === activePanel.value) return
  if (value) updateTabKey(value)
  activePanel.value = value
}

onMounted(() => {
  const initial = resolveTabKey(route.params.tab)
  tab.value = initial
  activate(initial)
})

watch(tab, async (newValue) => {
  if (!newValue || route.name !== owningRoute) return
  emit('tabChanged', newValue)
  // newValue should always match a tab key (resolveTabKey guards entry
  // points), but treat a miss as "drop back to the first tab" rather than
  // crashing.
  const match =
    props.tabs.find((el) => el[props.tabKey] === newValue) ?? props.tabs[0]
  const tabName = match[props.tabTitle]
  const destination = {
    name: route.name,
    params: { ...route.params, tab: newValue },
    query: route.query,
    hash: route.hash,
  }
  if (route.params.tab !== newValue) {
    if (resolveTabKey(route.params.tab) === newValue)
      await router.replace(destination)
    else await router.push(destination)
  }
  if (
    route.name !== destination.name ||
    tab.value !== newValue ||
    route.params.tab !== newValue
  )
    return
  activate(newValue)
  appStore.addBreadcrumbsLevel(
    tabName,
    {
      // tab in the route is the key (e.g. 'activity-subgroups'), not the
      // human label — clicking the crumb has to round-trip through the
      // same router push above.
      name: route.name,
      params: { ...route.params, tab: newValue },
    },
    props.breadcrumbsLevel,
    true
  )
})

watch(
  () => [route.params.tab, choices.value.map((item) => item.value).join('|')],
  ([newValue]) => {
    if (route.name !== owningRoute) return
    const resolved = resolveTabKey(newValue)
    tab.value = resolved
    activate(resolved)
  }
)

defineExpose({
  tab,
})
</script>

<style scoped>
.section-picker {
  max-width: 28rem;
  margin-bottom: 12px;
}
</style>
