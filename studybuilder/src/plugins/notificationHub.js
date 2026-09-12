import { ref } from 'vue'

const queue = ref([])

function getReadingTimeMs(text) {
  const lettersPerSecond = 7
  const letterCount = text.replaceAll(' ', '').length
  const seconds = letterCount / lettersPerSecond
  return Math.max(Math.ceil(seconds * 1000), 7000)
}

function add(notification) {
  notification = {
    ...notification,
    time: Date.now(),
    type: notification.type || 'success',
    timeout:
      notification.timeout ?? (getReadingTimeMs(notification.msg) || 3000),
  }

  const notificationIndex = indexOf(notification)
  if (notificationIndex >= 0 && notification.type !== 'success') {
    queue.value.splice(notificationIndex, 1)
  }
  queue.value = [notification, ...queue.value]

  if (notification.type === 'error') {
    console.log(notification.msg)
    const correlationId = notification?.error?.correlation_id
    if (correlationId) {
      console.log(`Correlation ID: ${correlationId}`)
    }
  }

  if (notification.timeout > 0) {
    setTimeout(() => remove(notification), notification.timeout)
  }
}

function remove(notification) {
  queue.value = queue.value.filter(
    (item) => item.time !== notification.time || item.msg !== notification.msg
  )
}

function indexOf(notification) {
  return queue.value.findIndex((item) => {
    return (
      item.msg === notification.msg &&
      item.type === notification.type &&
      item.error?.path === notification.error?.path
    )
  })
}

function clear() {
  queue.value = []
}

function clearErrors() {
  queue.value = queue.value.filter((item) => item.type != 'error')
}

export default {
  install(app) {
    app.provide('notificationHub', { queue, add, remove, clear, clearErrors })
  },
}

export const notificationHub = { queue, add, remove, clear, clearErrors }
