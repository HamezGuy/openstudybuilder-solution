<template>
  <v-alert v-if="listingError" type="error" role="alert">
    <p>{{ $t('_global.listing_read_failed') }}</p>
    <p v-if="typeof listingError === 'string'">{{ listingError }}</p>
    <v-btn class="mt-2" @click="retryListing">{{ $t('_global.retry') }}</v-btn>
  </v-alert>
  <NNTable
    table-id="studies-analysis-metadata-table"
    :headers="headers"
    item-value="listingRowKey"
    :items-length="total"
    :items="displayItems"
    show-column-names-toggle-button
    :column-data-resource="exportDataUrl"
    :export-config="{ objectLabel: exportObjectLabel, dataUrl: exportDataUrl }"
    @filter="fetchData"
  >
    <template
      v-for="header in defaultHeaders"
      #[`item.${header.key}`]="{ item }"
    >
      {{ analysisMetadataValue(item, header.key) }}
    </template>
    <template v-for="(_, slot) of $slots" #[slot]="scope">
      <slot :name="slot" v-bind="scope" />
    </template>
  </NNTable>
</template>

<script>
import { computed } from 'vue'
import filteringParameters from '@/utils/filteringParameters'
import listings from '@/api/listings'
import { useStudyListing } from '@/composables/useStudyListing'
import NNTable from '@/components/tools/NNTable.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import {
  analysisMetadataRowKey,
  analysisMetadataValue,
} from '@/utils/analysisMetadata'

export default {
  components: {
    NNTable,
  },
  props: {
    type: {
      type: String,
      default: '',
    },
    headers: {
      type: Array,
      default: () => [],
    },
  },
  setup(props) {
    const studiesGeneralStore = useStudiesGeneralStore()
    return {
      ...useStudyListing(
        () => [
          studiesGeneralStore.selectedStudy?.uid,
          studiesGeneralStore.selectedStudyVersion,
          props.type,
        ],
        (uid, type, params) => listings.getAllAdam(uid, type, params)
      ),
      selectedStudy: computed(() => studiesGeneralStore.selectedStudy),
      analysisMetadataValue,
    }
  },
  computed: {
    defaultHeaders() {
      return this.headers.filter((header) => !this.$slots[`item.${header.key}`])
    },
    displayItems() {
      return this.items.map((item) => ({
        ...item,
        listingRowKey: analysisMetadataRowKey(this.type, item),
      }))
    },
    exportDataUrl() {
      return `listings/studies/${this.selectedStudy.uid}/adam/${this.type}`
    },
    exportObjectLabel() {
      return `adam-${this.type}`
    },
  },
  methods: {
    fetchData(filters, options, filtersUpdated) {
      const params = filteringParameters.prepareParameters(
        options,
        filters,
        filtersUpdated
      )
      return this.load(params)
    },
  },
}
</script>
