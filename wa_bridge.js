import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  makeCacheableSignalKeyStore,
  fetchLatestBaileysVersion
} from '@whiskeysockets/baileys'
import express from 'express'
import pino from 'pino'
import fs from 'fs'

const logger = pino({ level: 'silent' })
const app = express()
app.use(express.json())

const AUTH_DIR = './auth_info'
const PORT = process.env.WA_PORT || 3001
const PHONE_NUMBER = process.env.WA_PHONE || ''

let sock = null
let isConnected = false
let connectionStatus = 'disconnected'
let pairingCode = null
let reconnectAttempts = 0
const MAX_RECONNECT = 5

async function connectWhatsApp() {
  if (reconnectAttempts >= MAX_RECONNECT) {
    console.log('❌ Слишком много попыток переподключения. Жду 5 минут...')
    reconnectAttempts = 0
    setTimeout(connectWhatsApp, 5 * 60 * 1000)
    return
  }

  try {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
    const { version } = await fetchLatestBaileysVersion()
    console.log(`📱 Используем WA версию: ${version.join('.')}`)

    sock = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger)
      },
      printQRInTerminal: false,
      logger,
      // Имитируем мобильный браузер
      browser: ['Ubuntu', 'Chrome', '22.0.0.75'],
      generateHighQualityLinkPreview: false,
      markOnlineOnConnect: false,
      // Важные настройки против блокировки
      connectTimeoutMs: 60000,
      keepAliveIntervalMs: 25000,
      retryRequestDelayMs: 2000,
      maxMsgRetryCount: 3,
      syncFullHistory: false,
    })

    if (!state.creds.registered && PHONE_NUMBER) {
      setTimeout(async () => {
        try {
          console.log(`📲 Запрашиваю pairing code для +${PHONE_NUMBER}...`)
          const code = await sock.requestPairingCode(PHONE_NUMBER)
          pairingCode = code
          console.log(`\n========================================`)
          console.log(`📱 PAIRING CODE: ${code}`)
          console.log(`========================================`)
          console.log(`Откройте WhatsApp → Настройки → Связанные устройства`)
          console.log(`→ Привязать устройство → Привязать по номеру телефона`)
          console.log(`→ Введите код: ${code}`)
          console.log(`========================================\n`)
        } catch (err) {
          console.error('Ошибка получения pairing code:', err.message)
        }
      }, 5000)
    }

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect } = update

      if (connection === 'close') {
        isConnected = false
        connectionStatus = 'disconnected'
        const statusCode = lastDisconnect?.error?.output?.statusCode
        console.log(`❌ Соединение закрыто. Код: ${statusCode}`)

        if (statusCode === DisconnectReason.loggedOut) {
          console.log('🚪 Разлогинен. Удаляю сессию...')
          if (fs.existsSync(AUTH_DIR)) {
            fs.rmSync(AUTH_DIR, { recursive: true })
          }
          connectionStatus = 'logged_out'
          reconnectAttempts = 0
          // Переподключаемся чтобы получить новый pairing code
          setTimeout(connectWhatsApp, 3000)
        } else if (statusCode === 405) {
          // WhatsApp блокирует — ждём дольше
          reconnectAttempts++
          const delay = Math.min(reconnectAttempts * 30000, 120000)
          console.log(`⏳ Код 405 (блокировка WA). Попытка ${reconnectAttempts}/${MAX_RECONNECT}. Жду ${delay/1000} сек...`)
          setTimeout(connectWhatsApp, delay)
        } else {
          reconnectAttempts++
          const delay = 10000
          console.log(`🔄 Переподключение через ${delay/1000} сек... (попытка ${reconnectAttempts})`)
          setTimeout(connectWhatsApp, delay)
        }
      } else if (connection === 'open') {
        isConnected = true
        connectionStatus = 'connected'
        pairingCode = null
        reconnectAttempts = 0
        console.log('✅ WhatsApp подключён!')
      } else if (connection === 'connecting') {
        connectionStatus = 'connecting'
        console.log('⏳ Подключение...')
      }
    })

  } catch (err) {
    console.error('Ошибка connectWhatsApp:', err.message)
    reconnectAttempts++
    setTimeout(connectWhatsApp, 15000)
  }
}

// ─── API ───

app.get('/status', (req, res) => {
  res.json({
    connected: isConnected,
    status: connectionStatus,
    pairing_code: pairingCode,
    reconnect_attempts: reconnectAttempts
  })
})

app.post('/pair', async (req, res) => {
  const phone = req.body.phone || PHONE_NUMBER
  if (!phone) {
    return res.json({ success: false, error: 'Номер не указан' })
  }

  try {
    if (fs.existsSync(AUTH_DIR)) {
      fs.rmSync(AUTH_DIR, { recursive: true })
    }
    if (sock) {
      try { sock.end(undefined) } catch(e) {}
    }
    process.env.WA_PHONE = phone
    reconnectAttempts = 0
    setTimeout(connectWhatsApp, 2000)
    res.json({ success: true, message: 'Переподключение запущено. Проверьте /status через 15 сек.' })
  } catch (err) {
    res.json({ success: false, error: err.message })
  }
})

app.post('/send', async (req, res) => {
  const { phone, message } = req.body

  if (!isConnected) {
    return res.json({
      success: false,
      error: 'WhatsApp не подключён',
      status: connectionStatus,
      pairing_code: pairingCode
    })
  }

  if (!phone || !message) {
    return res.json({ success: false, error: 'Нужны phone и message' })
  }

  try {
    let cleanPhone = phone.replace(/[^0-9]/g, '')
    if (cleanPhone.startsWith('8') && cleanPhone.length === 11) {
      cleanPhone = '7' + cleanPhone.slice(1)
    }
    if (!cleanPhone.startsWith('7')) {
      cleanPhone = '7' + cleanPhone
    }

    const jid = cleanPhone + '@s.whatsapp.net'
    const [result] = await sock.onWhatsApp(jid)

    if (!result || !result.exists) {
      return res.json({
        success: false,
        error: `Номер +${cleanPhone} не зарегистрирован в WhatsApp`
      })
    }

    const sentMsg = await sock.sendMessage(result.jid, { text: message })
    console.log(`✅ Отправлено → +${cleanPhone}`)
    res.json({ success: true, phone: '+' + cleanPhone, message_id: sentMsg.key.id })

  } catch (err) {
    console.error(`❌ Ошибка отправки: ${err.message}`)
    res.json({ success: false, error: err.message })
  }
})

app.post('/send_bulk', async (req, res) => {
  const { phones, message, delay_seconds } = req.body
  const delay = (delay_seconds || 30) * 1000

  if (!isConnected) {
    return res.json({ success: false, error: 'WhatsApp не подключён' })
  }

  res.json({
    success: true,
    message: `Рассылка запущена на ${phones.length} номеров`,
    estimated_time: `~${Math.ceil(phones.length * delay / 60000)} мин`
  })

  for (let i = 0; i < phones.length; i++) {
    const phone = phones[i]
    try {
      let cleanPhone = phone.replace(/[^0-9]/g, '')
      if (cleanPhone.startsWith('8') && cleanPhone.length === 11) {
        cleanPhone = '7' + cleanPhone.slice(1)
      }
      const jid = cleanPhone + '@s.whatsapp.net'
      const [result] = await sock.onWhatsApp(jid)
      if (result && result.exists) {
        await sock.sendMessage(result.jid, { text: message })
        console.log(`✅ [${i+1}/${phones.length}] → +${cleanPhone}`)
      }
      if (i < phones.length - 1) {
        await new Promise(r => setTimeout(r, delay + Math.random() * 10000))
      }
    } catch (err) {
      console.error(`❌ [${i+1}] Ошибка → ${phone}: ${err.message}`)
    }
  }
})

app.post('/logout', async (req, res) => {
  try {
    if (sock) { await sock.logout() }
    if (fs.existsSync(AUTH_DIR)) {
      fs.rmSync(AUTH_DIR, { recursive: true })
    }
    res.json({ success: true })
  } catch (err) {
    res.json({ success: false, error: err.message })
  }
})

app.listen(PORT, () => {
  console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
  connectWhatsApp()
})
