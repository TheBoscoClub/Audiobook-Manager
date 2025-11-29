# Quick Start Guide

Welcome to your Personal Audiobook Library! Follow these simple steps to get started.

## Step 1: Scan Your Audiobook Collection

The scanner will extract metadata from all your .m4b audiobooks and create a searchable database.

```bash
cd /raid0/ClaudeCodeProjects/audiobook-library/scanner
python3 scan_audiobooks.py
```

This will:
- Scan all .m4b files in `/raid0/Audiobooks`
- Extract metadata (title, author, narrator, duration, etc.)
- Extract cover art images
- Generate `/raid0/ClaudeCodeProjects/audiobook-library/data/audiobooks.json`
- Save cover art to `/raid0/ClaudeCodeProjects/audiobook-library/web/covers/`

**Note:** This may take 30-60 minutes for 1,756 audiobooks. The script will show progress as it runs.

## Step 2: Launch the Web Interface

Once scanning is complete, start the web server:

```bash
cd /raid0/ClaudeCodeProjects/audiobook-library/web
python3 -m http.server 8080
```

## Step 3: Open in Your Browser

Navigate to: **http://localhost:8080**

You should see your beautiful vintage library interface!

## Using the Library

### Search
- Use the search bar to find audiobooks by title, author, narrator, or description
- Search is instant and case-insensitive

### Browse by Index
Click any category in the left sidebar to expand it:
- **Authors** - Browse by author name
- **Genres** - Organized by genre and sub-genre
- **Literary Eras** - Books grouped by publication period
- **Publishers** - Filter by publishing house
- **Narrators** - Find books by your favorite narrator
- **Series** - Browse book series
- **Topics** - Thematic categorization

### View Options
- **Grid View** - Visual browsing with cover art
- **List View** - Detailed list with descriptions
- **Sort Options** - Sort by title, author, year, or duration

### Book Details
Click any audiobook to see:
- Full description
- Complete metadata
- Cover art (full size)
- File location
- Topics and categorization

## Tips

1. **Re-scan when adding new audiobooks**: Just run the scanner again to update your library

2. **Filtering**: You can combine search with index filters for precise results

3. **Performance**: The library handles 1,700+ books smoothly. Cover art extraction is the slowest part of scanning.

4. **Customization**: Edit `web/css/library.css` to customize the appearance

## Troubleshooting

**Problem:** Scanner fails with "ffprobe not found"
- **Solution:** Install ffmpeg: `sudo pacman -S ffmpeg`

**Problem:** No cover art showing
- **Solution:** Ensure ffmpeg is installed (not just ffprobe)

**Problem:** Web page shows "Error loading audiobooks"
- **Solution:** Make sure you've run the scanner first to generate the data file

**Problem:** Changes to CSS/JS not showing
- **Solution:** Hard refresh your browser (Ctrl+Shift+R)

## Advanced

### Customizing the Scanner

Edit `/raid0/ClaudeCodeProjects/audiobook-library/scanner/scan_audiobooks.py`:

- `AUDIOBOOK_DIR` - Change the source directory
- `COVER_DIR` - Change where cover art is saved
- `categorize_genre()` - Customize genre categorization
- `determine_literary_era()` - Adjust era definitions
- `topic_keywords` - Add or modify topic detection

### Running on a Different Port

```bash
cd web
python3 -m http.server 9000  # Use port 9000 instead
```

Then navigate to: http://localhost:9000

Enjoy your library!
