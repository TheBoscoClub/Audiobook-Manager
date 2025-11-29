# Audiobook Library - Quick Start

## 🚀 Launch the Library

```bash
cd /raid0/ClaudeCodeProjects/audiobook-library
./launch-v2.sh
```

Your browser will automatically open to: **http://localhost:8090**

---

## ✅ Verify It's Working

You should see:
- **"Personal Audiobook Library"** at the top
- **2,702 volumes • 31,282 hours** in the statistics
- **Book grid loading instantly** (not stuck on "Loading audiobooks...")
- **Pagination controls** at the bottom

---

## ⚠️ If You See "Loading audiobooks..." Forever

You're on the **old version**. Navigate to:
```
http://localhost:8090
```

NOT:
```
http://localhost:8090/web/       ← Old version
http://localhost:8080/web/       ← Old version
```

---

## 🔍 Features

- **Search** - Full-text search across all fields
- **Filter** - By author, narrator, format
- **Sort** - By title, author, duration, date added
- **Pagination** - Browse 25/50/100/200 books per page
- **Fast** - Loads in <500ms (vs 8-15 seconds before)

---

## 🛠️ Troubleshooting

### "Error loading audiobooks"

**Problem:** API server not running

**Solution:**
```bash
cd /raid0/ClaudeCodeProjects/audiobook-library
source venv/bin/activate
python backend/api.py
```

### Page loads but no books appear

**Problem:** Check browser console (F12) for errors

**Solution:**
1. Verify API is running: `curl http://localhost:5001/health`
2. Check browser console for JavaScript errors
3. Restart: `./launch-v2.sh`

### Port already in use

**Problem:** Port 5001 or 8090 in use

**Solution:**
```bash
# Stop existing servers
pkill -f "api.py"
pkill -f "http.server"

# Restart
./launch-v2.sh
```

---

## 📊 API Endpoints

The library includes a REST API on port 5001:

```bash
# Get statistics
curl http://localhost:5001/api/stats

# Search audiobooks
curl "http://localhost:5001/api/audiobooks?search=tolkien"

# Filter by author
curl "http://localhost:5001/api/audiobooks?author=sanderson"

# Get all filters (authors, narrators, etc.)
curl http://localhost:5001/api/filters
```

---

## 🔄 Update Library After Adding Audiobooks

```bash
# 1. Scan new audiobooks
cd /raid0/ClaudeCodeProjects/audiobook-library/scanner
python3 scan_audiobooks.py

# 2. Update database
cd /raid0/ClaudeCodeProjects/audiobook-library
source venv/bin/activate
python backend/import_to_db.py

# 3. Refresh browser (click "↻ Refresh" button)
```

---

## 📁 File Locations

- **Database:** `backend/audiobooks.db` (4 MB)
- **API Server:** `backend/api.py` (port 5001)
- **Web Interface:** `web-v2/` (port 8090)
- **Launcher:** `launch-v2.sh`

---

## 📚 Documentation

- `UPGRADE_GUIDE.md` - Full features and deployment guide
- `PERFORMANCE_REPORT.md` - Benchmarks and analysis
- `backend/README.md` - API documentation

---

**Enjoy your lightning-fast audiobook library! 📚⚡**

For issues or questions, check the documentation files listed above.
