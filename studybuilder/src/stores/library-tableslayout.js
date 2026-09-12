import { defineStore } from 'pinia'

export const useTablesLayoutStore = defineStore('tablesLayout', {
  state: () => ({
    columns: {},
  }),

  actions: {
    initiateColumns() {
      let columnsFromLocalStorage = {}
      if (localStorage.getItem('columns') != null) {
        columnsFromLocalStorage = JSON.parse(localStorage.getItem('columns'))
      }
      this.columns = columnsFromLocalStorage
    },
    setColumns(columns) {
      for (const [key, value] of columns) {
        this.columns[key] = value
      }
      localStorage.setItem('columns', JSON.stringify(this.columns))
    },
  },
})
