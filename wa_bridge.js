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

const AUTH_DIR = process.env.AUTH_DIR || '/tmp/wa_auth_info'
const PORT = process.env.WA_PORT || 3001
const PHONE_NUMBER = process.env.WA_PHONE || ''

let sock = null
let isConnected = false
let connectionStatus = 'disconnected'
let pairingCode = null
let reconnectAttempts = 0
const MAX_RECONNECT = 3

function normalizePhone(phone) {
    // Только цифры, без + и пробелов
    return String(phone).replace(/[^0-9]/g, '')
}

function ensureAuthDir() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true })
        console.log(`✅ Создана папка ${AUTH_DIR}`)
    }
}

function hasExistingSession() {
    ensureAuthDir()
    const files = fs.readdirSync(AUTH_DIR)
    return files.length > 0
}

async function connectToWhatsApp() {
    ensureAuthDir()

    try {
        const { version } = await fetchLatestBaileysVersion()
        console.log(`📱 WA версия: ${version}`)
        console.log(`📁 Сессия: ${hasExistingSession() ? 'найдена' : 'отсутствует'}`)

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

        sock = makeWASocket({
            version,
            auth: {
                creds: state.creds,
                keys: makeCacheableSignalKeyStore(state.keys, logger),
            },
            printQRInTerminal: false,
            logger,
            browser: ['Ubuntu', 'Chrome', '120.0.0'],
            defaultQueryTimeoutMs: 20000,
            generateHighQualityLinkPreview: false,
            syncFullHistory: false,
            markOnlineOnConnect: false,
            connectTimeoutMs: 60000,
            keepAliveIntervalMs: 10000,
            retryRequestDelayMs: 2000,
        })

        sock.ev.on('connection.update', async (update) => {
            console.log('🔔 update:', JSON.stringify({
                connection: update.connection,
                statusCode: update.lastDisconnect?.error?.output?.statusCode,
                hasPairingCode: !!update.pairingCode,
                qr: !!update.qr,
            }))

            const { connection, lastDisconnect, pairingCode: pc } = update

            if (pc) {
                pairingCode = pc
                connectionStatus = 'waiting_pair'
                console.log(`🔐 PAIRING CODE: ${pc}`)
            }

            if (connection === 'open') {
                isConnected = true
                connectionStatus = 'connected'
                reconnectAttempts = 0
                pairingCode = null
                console.log('✅ WhatsApp подключён!')
                console.log(`👤 ID: ${sock?.user?.id}`)
            }

            if (connection === 'close') {
                isConnected = false
                connectionStatus = 'disconnected'
                const statusCode = lastDisconnect?.error?.output?.statusCode
                const reason = lastDisconnect?.error?.message || 'unknown'
                console.log(`❌ Закрыто. Код: ${statusCode}, причина: ${reason}`)

                if (statusCode === DisconnectReason.loggedOut) {
                    console.log('🚪 Разлогинен. Удаляю сессию...')
                    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }) } catch {}
                    ensureAuthDir()
                    connectionStatus = 'logged_out'
                    sock = null
                    // НЕ переподключаемся — ждём ручного pairing
                } else if (statusCode === 515) {
                    console.log('🔄 Код 515 — перезапуск...')
                    sock = null
                    setTimeout(() => connectToWhatsApp(), 3000)
                } else if (reconnectAttempts < MAX_RECONNECT) {
                    reconnectAttempts++
                    const delay = Math.min(reconnectAttempts * 8000, 30000)
                    console.log(`🔄 Переподключение через ${delay/1000}с (${reconnectAttempts}/${MAX_RECONNECT})...`)
                    sock = null
                    setTimeout(() => connectToWhatsApp(), delay)
                } else {
                    console.log('❌ Максимум попыток исчерпан.')
                    connectionStatus = 'failed'
                    sock = null
                }
            }
        })

        sock.ev.on('creds.update', saveCreds)

    } catch (error) {
        console.error('❌ Ошибка connectToWhatsApp:', error.message)
        if (reconnectAttempts < MAX_RECONNECT) {
            reconnectAttempts++
            setTimeout(() => connectToWhatsApp(), 10000)
        }
    }
}

// ── API ─────────────────────────────────────────────────────────────────────

app.get('/health', (req, res) => {
    res.json({ status: 'ok', connected: isConnected, connectionStatus })
})

app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        connectionStatus,
        phone: sock?.user?.id ? sock.user.id.split(':')[0] : null,
        pairingCode: pairingCode || null,
    })
})

app.post('/pair', async (req, res) => {
    const { phone } = req.body
    const targetPhone = phone || PHONE_NUMBER

    if (!targetPhone) {
        return res.status(400).json({
            error: 'Укажите номер телефона или установите WA_PHONE в переменных окружения.'
        })
    }

    if (isConnected) {
        return res.json({ success: true, message: 'Уже подключён', alreadyConnected: true })
    }

    try {
        // Закрываем старый сокет
        if (sock) {
            console.log('🔄 Закрываю старый сокет...')
            try { sock.end(undefined) } catch {}
            sock = null
            isConnected = false
            await new Promise(resolve => setTimeout(resolve, 3000))
        }

        ensureAuthDir()
        const { version } = await fetchLatestBaileysVersion()
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

        console.log('🔌 Создаю новый сокет для pairing...')

        const newSock = makeWASocket({
            version,
            auth: {
                creds: state.creds,
                keys: makeCacheableSignalKeyStore(state.keys, logger),
            },
            printQRInTerminal: false,
            logger,
            browser: ['Ubuntu', 'Chrome', '120.0.0'],
            defaultQueryTimeoutMs: 20000,
            generateHighQualityLinkPreview: false,
            syncFullHistory: false,
            markOnlineOnConnect: false,
        })

        newSock.ev.on('creds.update', saveCreds)

        // Ждём установки соединения перед запросом кода
        console.log('⏳ Ожидаю соединения с WA серверами...')
        await new Promise(resolve => setTimeout(resolve, 5000))

        const cleanPhone = normalizePhone(targetPhone)
        console.log(`📲 Запрашиваю pairing code для: ${cleanPhone}`)

        const code = await newSock.requestPairingCode(cleanPhone)
        pairingCode = code
        connectionStatus = 'waiting_pair'
        console.log(`📱 Pairing code: ${code}`)

        sock = newSock

        // Обработчики для нового сокета
        sock.ev.on('connection.update', async (update) => {
            console.log('🔔 pair-update:', JSON.stringify({
                connection: update.connection,
                statusCode: update.lastDisconnect?.error?.output?.statusCode,
                hasPairingCode: !!update.pairingCode,
            }))

            const { connection, lastDisconnect, pairingCode: pc } = update

            if (pc) {
                pairingCode = pc
                console.log(`🔐 Новый код: ${pc}`)
            }

            if (connection === 'open') {
                isConnected = true
                connectionStatus = 'connected'
                reconnectAttempts = 0
                pairingCode = null
                console.log('✅ WhatsApp подключён после pairing!')
            }

            if (connection === 'close') {
                isConnected = false
                const statusCode = lastDisconnect?.error?.output?.statusCode
                console.log(`❌ Закрыто после pairing. Код: ${statusCode}`)

                if (statusCode === DisconnectReason.loggedOut) {
                    connectionStatus = 'logged_out'
                    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }) } catch {}
                    ensureAuthDir()
                    sock = null
                } else {
                    // Пробуем переподключиться через основной метод
                    connectionStatus = 'reconnecting'
                    sock = null
                    setTimeout(() => connectToWhatsApp(), 5000)
                }
            }
        })

        res.json({ success: true, pairingCode: code })

    } catch (error) {
        console.error('❌ Pairing error:', error.message)
        console.error(error.stack)
        res.status(500).json({ error: error.message })
    }
})

app.post('/logout', async (req, res) => {
    try {
        if (sock) {
            try { sock.end(undefined) } catch {}
            sock = null
        }
        if (fs.existsSync(AUTH_DIR)) {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true })
        }
        isConnected = false
        connectionStatus = 'logged_out'
        pairingCode = null
        ensureAuthDir()
        res.json({ success: true })
    } catch (error) {
        res.status(500).json({ error: error.message })
    }
})

app.post('/send', async (req, res) => {
    const { phone, message } = req.body
    if (!phone || !message) {
        return res.status(400).json({ error: 'phone и message обязательны' })
    }
    if (!sock || !isConnected) {
        return res.status(503).json({ error: 'WhatsApp не подключён', status: connectionStatus })
    }
    try {
        const jid = normalizePhone(phone) + '@s.whatsapp.net'
        await sock.sendMessage(jid, { text: message })
        res.json({ success: true, to: phone })
    } catch (error) {
        console.error('Send error:', error.message)
        res.status(500).json({ error: error.message })
    }
})

app.post('/send_bulk', async (req, res) => {
    const { phones, message, delay = 30 } = req.body
    if (!phones?.length || !message) {
        return res.status(400).json({ error: 'phones[] и message обязательны' })
    }
    if (!sock || !isConnected) {
        return res.status(503).json({ error: 'WhatsApp не подключён', status: connectionStatus })
    }

    const results = { success: true, sent: [], failed: [], total: phones.length }
    const startTime = Date.now()

    for (let i = 0; i < phones.length; i++) {
        const phone = phones[i]
        try {
            const jid = normalizePhone(phone) + '@s.whatsapp.net'
            await sock.sendMessage(jid, { text: message })
            results.sent.push(phone)
            console.log(`✅ ${i + 1}/${phones.length}: ${phone}`)
        } catch (error) {
            console.error(`❌ ${phone}:`, error.message)
            results.failed.push({ phone, error: error.message })
        }
        if (i < phones.length - 1 && delay > 0) {
            await new Promise(resolve => setTimeout(resolve, delay * 1000))
        }
    }

    results.elapsed_seconds = Math.round((Date.now() - startTime) / 1000)
    res.json(results)
})

// Запуск
app.listen(PORT, '0.0.0.0', () => {
    console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
})

// Запускаем подключение только если есть сохранённая сессия
if (hasExistingSession()) {
    console.log('📂 Найдена сохранённая сессия, подключаюсь...')
    connectToWhatsApp().catch(console.error)
} else {
    console.log('ℹ️ Сессии нет. Используйте кнопку 🔗 Пара WA для подключения.')
    connectionStatus = 'not_authorized'
}
