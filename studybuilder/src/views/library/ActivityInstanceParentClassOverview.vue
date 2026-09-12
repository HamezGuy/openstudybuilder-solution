<template>
  <div v-if="activityInstanceParentClassOverview" class="px-4">
    <div class="d-flex page-title">
      {{
        activityInstanceParentClassOverview.parent_activity_instance_class.name
      }}
    </div>
    <ActivityInstanceParentClassOverview
      v-if="activityInstanceParentClassOverview"
      ref="overviewComponent"
    />
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import ActivityInstanceParentClassOverview from '@/components/library/ActivityInstanceParentClassOverview.vue'
import activityInstanceClasses from '@/api/activityInstanceClasses'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()

const activityInstanceParentClassOverview = ref(null)
const overviewComponent = ref(null)

const fetchOverview = async () => {
  try {
    const response = await activityInstanceClasses.getParentClassOverview(
      route.params.id,
      route.params.version
    )
    activityInstanceParentClassOverview.value = response.data

    // Pass data to component after it's mounted
    await nextTick()
    if (overviewComponent.value) {
      overviewComponent.value.itemOverview = response.data
    }

    appStore.addBreadcrumbsLevel(
      activityInstanceParentClassOverview.value.parent_activity_instance_class
        .name,
      { name: 'ActivityInstanceParentClassOverview', params: route.params },
      5,
      true
    )
  } catch (error) {
    console.error(
      'Error fetching activity instance parent class overview:',
      error
    )
  }
}

// Set up breadcrumbs
onMounted(() => {
  appStore.addBreadcrumbsLevel('Library', { name: 'Library' }, 1, false)

  appStore.addBreadcrumbsLevel('Concepts', { name: 'Library' }, 2, false)

  appStore.addBreadcrumbsLevel('Activities', { name: 'Activities' }, 3, true)

  appStore.addBreadcrumbsLevel(
    'Activity Instance Parent Classes',
    { name: 'Activities', params: { tab: 'activity-instance-classes' } },
    4,
    true
  )
})

watch(
  () => route.params,
  () => {
    fetchOverview()
  },
  { immediate: true, deep: true }
)
</script>
