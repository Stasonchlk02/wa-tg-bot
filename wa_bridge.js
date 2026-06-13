import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  makeCacheableSignalKeyStore
} from '@whiskeysockets/baileys'
import express from 'express'
import pino from 'pino'
import fs from 'fs'
import path from 'path'

const logger = pino({ level: 'silent' })
const app = express()
app.use(express.json())

const AUTH_DIR = './auth_info'
const PORT = process.env.WA_PORT || 3001
const PHONE_NUMBER = process.env.WA_PHONE || '' // твой номер: 79XXXXXXXXX

let sock = null
let isConnected = false
let connectionStatus = 'disconnected'
let pairingCode = null

// ─── Подключение к WhatsApp ───
async function connectWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

  sock = makeWASocket({
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger)
    },
    printQRInTerminal: false, // не используем QR
    logger,
    browser: ['WhatsApp Bot', 'Chrome', '120.0.0'],
    generateHighQualityLinkPreview: false,
    markOnlineOnConnect: false
  })

  // Если не авторизован — запрашиваем pairing code
  if (!state.creds.registered && PHONE_NUMBER) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(PHONE_NUMBER)
        pairingCode = code
        console.log(`\n\n========================================`)
        console.log(`📱 PAIRING CODE: ${code}`)
        console.log(`========================================`)
        console.log(`Откройте WhatsApp → Настройки → Связанные устройства`)
        console.log(`→ Привязать устройство → Привязать по номеру телефона`)
        console.log(`→ Введите код: ${code}`)
        console.log(`========================================\n\n`)
      } catch (err) {
        console.error('Ошибка получения pairing code:', err.message)
      }
    }, 3000)
  }

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect } = update

    if (connection === 'close') {
      isConnected = false
      connectionStatus = 'disconnected'
      const statusCode = lastDisconnect?.error?.output?.statusCode

      console.log(`❌ Соединение закрыто. Код: ${statusCode}`)

      if (statusCode !== DisconnectReason.loggedOut) {
        console.log('🔄 Переподключение через 5 сек...')
        setTimeout(connectWhatsApp, 5000)
      } else {
        console.log('🚪 Разлогинен. Удаляю сессию...')
        if (fs.existsSync(AUTH_DIR)) {
          fs.rmSync(AUTH_DIR, { recursive: true })
        }
        connectionStatus = 'logged_out'
      }
    } else if (connection === 'open') {
      isConnected = true
      connectionStatus = 'connected'
      pairingCode = null
      console.log('✅ WhatsApp подключён!')
    } else if (connection === 'connecting') {
      connectionStatus = 'connecting'
      console.log('⏳ Подключение...')
    }
  })

  sock.ev.on('messages.upsert', (m) => {
    // логируем входящие для отладки
    if (m.type === 'notify') {
      for (const msg of m.messages) {
        if (!msg.key.fromMe) {
          const from = msg.key.remoteJid
          const text = msg.message?.conversation
            || msg.message?.extendedTextMessage?.text
            || '[медиа]'
          console.log(`📩 Входящее от ${from}: ${text}`)
        }
      }
    }
  })
}

// ─── API Endpoints ───

// Статус подключения
app.get('/status', (req, res) => {
  res.json({
    connected: isConnected,
    status: connectionStatus,
    pairing_code: pairingCode
  })
})

// Получить pairing code повторно
app.post('/pair', async (req, res) => {
  const phone = req.body.phone || PHONE_NUMBER
  if (!phone) {
    return res.json({ success: false, error: 'Номер не указан' })
  }

  try {
    // Удаляем старую сессию
    if (fs.existsSync(AUTH_DIR)) {
      fs.rmSync(AUTH_DIR, { recursive: true })
    }

    // Перезапускаем
    if (sock) {
      sock.end(undefined)
    }

    // Обновляем номер
    process.env.WA_PHONE = phone

    setTimeout(async () => {
      await connectWhatsApp()
    }, 1000)

    res.json({ success: true, message: 'Переподключение запущено. Проверьте /status через 10 сек.' })
  } catch (err) {
    res.json({ success: false, error: err.message })
  }
})

// Отправка сообщения
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
    // Форматируем номер
    let cleanPhone = phone.replace(/[^0-9]/g, '')
    if (cleanPhone.startsWith('8') && cleanPhone.length === 11) {
      cleanPhone = '7' + cleanPhone.slice(1)
    }
    if (!cleanPhone.startsWith('7')) {
      cleanPhone = '7' + cleanPhone
    }

    const jid = cleanPhone + '@s.whatsapp.net'

    // Проверяем есть ли номер в WhatsApp
    const [result] = await sock.onWhatsApp(jid)

    if (!result || !result.exists) {
      return res.json({
        success: false,
        error: `Номер +${cleanPhone} не зарегистрирован в WhatsApp`
      })
    }

    // Отправляем
    const sentMsg = await sock.sendMessage(result.jid, { text: message })

    console.log(`✅ Отправлено → +${cleanPhone}: ${message.substring(0, 50)}...`)

    res.json({
      success: true,
      phone: '+' + cleanPhone,
      message_id: sentMsg.key.id
    })

  } catch (err) {
    console.error(`❌ Ошибка отправки: ${err.message}`)
    res.json({ success: false, error: err.message })
  }
})

// Массовая отправка
app.post('/send_bulk', async (req, res) => {
  const { phones, message, delay_seconds } = req.body
  const delay = (delay_seconds || 30) * 1000 // минимум 30 сек между сообщениями

  if (!isConnected) {
    return res.json({ success: false, error: 'WhatsApp не подключён' })
  }

  if (!phones || !Array.isArray(phones) || !message) {
    return res.json({ success: false, error: 'Нужны phones (массив) и message' })
  }

  // Запускаем в фоне
  const results = []

  // Сразу отвечаем что задача запущена
  res.json({
    success: true,
    message: `Рассылка запущена на ${phones.length} номеров`,
    estimated_time: `~${Math.ceil(phones.length * delay / 60000)} мин`
  })

  // Отправляем в фоне
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
        console.log(`✅ [${i+1}/${phones.length}] Отправлено → +${cleanPhone}`)
      } else {
        console.log(`⏭️ [${i+1}/${phones.length}] Пропуск → +${cleanPhone} (нет в WA)`)
      }

      // Задержка между сообщениями (антибан)
      if (i < phones.length - 1) {
        const jitter = Math.random() * 10000 // случайная добавка 0-10 сек
        await new Promise(r => setTimeout(r, delay + jitter))
      }

    } catch (err) {
      console.error(`❌ [${i+1}/${phones.length}] Ошибка → ${phone}: ${err.message}`)
    }
  }
})

// Отключение
app.post('/logout', async (req, res) => {
  try {
    if (sock) {
      await sock.logout()
    }
    if (fs.existsSync(AUTH_DIR)) {
      fs.rmSync(AUTH_DIR, { recursive: true })
    }
    res.json({ success: true, message: 'Вышли из WhatsApp' })
  } catch (err) {
    res.json({ success: false, error: err.message })
  }
})

// ─── Старт ───
app.listen(PORT, () => {
  console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
  connectWhatsApp()
})
