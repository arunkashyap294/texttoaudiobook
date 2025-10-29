import streamlit as st
from gtts import gTTS
import fitz  # PyMuPDF
import os
from io import BytesIO
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pydub import AudioSegment
import time
import re

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extracts text content from PDF bytes."""
    try:
        # Open the PDF file from in-memory bytes
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text = "".join(page.get_text() for page in doc)
        return text
    except Exception as e:
        st.error(f"Error reading PDF file: {e}")
        return ""

def extract_text_from_txt_bytes(txt_bytes: bytes) -> str:
    """Extracts text content from TXT bytes."""
    try:
        return txt_bytes.decode('utf-8')
    except Exception as e:
        st.error(f"Error reading TXT file: {e}")
        return ""

def extract_text_from_html(html_content: str) -> str:
    """Extracts text from the HTML content of the older letters."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        # A simple approach is to get all text. This is usually effective for these older pages.
        return soup.get_text(separator='\n', strip=True)
    except Exception as e:
        st.error(f"Error parsing HTML: {e}")
        return ""

def clean_and_prepare_text(text: str) -> str:
    """
    Cleans the extracted text to improve audiobook flow.
    - Replaces decorative characters and multiple spaces.
    - Intelligently joins lines into paragraphs.
    """
    # 1. Remove long sequences of dashes, equals signs, or asterisks
    text = re.sub(r'[-=_*]{3,}', '', text)
    
    # 2. Replace multiple newlines with a single one to mark paragraph breaks
    text = re.sub(r'\n\s*\n', '\n', text)
    
    # 3. Replace single newlines (line breaks within a paragraph) with a space
    text = text.replace('\n', ' ')
    
    return text

def _get_text_chunks(text: str, max_chunk_size: int = 3000):
    """
    Splits text into chunks that are smaller than the max_chunk_size,
    respecting paragraph breaks.
    """
    chunks = []
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    for para in paragraphs:
        if len(para) <= max_chunk_size:
            chunks.append(para)
        else:
            # The paragraph is too long, so we split it into smaller parts.
            for i in range(0, len(para), max_chunk_size):
                chunks.append(para[i:i + max_chunk_size])
    return chunks

def _convert_chunk_to_audio(chunk: str, session: requests.Session, tld: str) -> AudioSegment | None:
    """
    Converts a single text chunk to an AudioSegment using gTTS,
    with session management and retry logic.
    """
    retries = 3
    delay = 5  # Start with a 5-second delay on failure
    for attempt in range(retries):
        try:
            tts = gTTS(text=chunk, lang='en', tld=tld, slow=False)
            fp = BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            return AudioSegment.from_mp3(fp)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429: # "Too Many Requests"
                st.warning(f"Rate limit hit. Retrying in {delay} seconds... (Attempt {attempt + 1}/{retries})")
                time.sleep(delay)
                delay *= 2 # Exponential backoff
            else:
                raise e # Re-raise other HTTP errors
    return None

def convert_text_to_mp3_chunked(text: str, tld: str) -> BytesIO:
    """
    Converts a large string of text into an in-memory MP3 file by processing it in chunks
    to avoid API rate limits.
    """
    if not text.strip():
        return None

    text_chunks = _get_text_chunks(text)
    combined_audio = AudioSegment.empty()
    
    progress_bar = st.progress(0, text="Converting audio chunks...")

    with requests.Session() as session:
        for i, chunk in enumerate(text_chunks):
            chunk_audio = _convert_chunk_to_audio(chunk, session, tld=tld)

            if chunk_audio:
                combined_audio += chunk_audio
            else:
                st.warning(f"Skipping a chunk of {len(chunk)} characters after multiple failed attempts.")
                continue

            progress_bar.progress((i + 1) / len(text_chunks), text=f"Processing audio chunk {i+1}/{len(text_chunks)}")
            
    final_fp = BytesIO()
    combined_audio.export(final_fp, format="mp3")
    final_fp.seek(0)
    return final_fp

# --- Streamlit UI ---

st.set_page_config(layout="centered", page_title="Document to Audiobook")

st.title("📖➡️🎧 Document to Audiobook Generator")

# --- Sidebar for Options ---
st.sidebar.title("⚙️ Options")

input_method = st.sidebar.radio(
    "Choose your input source",
    ("Berkshire Hathaway Letters", "Upload a File", "From a URL")
)

# Accent selection is common to all methods
st.sidebar.markdown("---")
ACCENT_OPTIONS = {
    "American (US)": "com",
    "British (UK)": "co.uk",
    "Australian": "com.au",
    "Indian": "co.in",
    "South African": "co.za"
}
selected_accent_name = st.sidebar.selectbox("Select Accent", options=list(ACCENT_OPTIONS.keys()))
selected_accent_tld = ACCENT_OPTIONS[selected_accent_name]

# --- Main Page Content ---
# Use session state to hold the final audio data across reruns
if 'mp3_audio' not in st.session_state:
    st.session_state.mp3_audio = None
if 'audio_filename' not in st.session_state:
    st.session_state.audio_filename = None

if input_method == "Berkshire Hathaway Letters":
    st.header("From Berkshire Hathaway Letters")
    st.markdown("Select a range of years to generate an audiobook of the shareholder letters.")
    min_available_year = 1977
    max_available_year = 2024
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.number_input("Start Year", min_value=min_available_year, max_value=max_available_year, value=min_available_year)
    with col2:
        end_year = st.number_input("End Year", min_value=min_available_year, max_value=max_available_year, value=max_available_year)

    if start_year > end_year:
        st.error("Start year cannot be after end year.")
    elif st.button(f"🚀 Generate Audiobook ({start_year}-{end_year})"):
        all_text = ""
        base_url = "https://www.berkshirehathaway.com/letters/letters.html"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        
        try:
            with st.spinner("Finding all shareholder letters..."):
                response = requests.get(base_url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')
                links = []
                for year in range(start_year, end_year + 1):
                    link_tag = soup.find('a', string=str(year))
                    if link_tag and link_tag.get('href'):
                        full_url = urljoin(base_url, link_tag['href'])
                        links.append((year, full_url))
                if not links:
                    st.error("Could not find any letter links for the selected range.")
                    st.stop()

            st.success(f"Found {len(links)} letters to process.")
            progress_bar = st.progress(0, "Processing letters...")
            for i, (year, url) in enumerate(sorted(links, key=lambda x: x[0])):
                doc_response = requests.get(url, headers=headers)
                doc_response.raise_for_status()
                all_text += f"\n\n--- Berkshire Hathaway Shareholder Letter: {year} ---\n\n"
                if url.endswith('.pdf'):
                    letter_text = extract_text_from_pdf_bytes(doc_response.content)
                else:
                    letter_text = extract_text_from_html(doc_response.text)
                all_text += clean_and_prepare_text(letter_text)
                progress_bar.progress((i + 1) / len(links), f"Processing letter for {year}...")
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to fetch URL: {e}")
            st.stop()
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")
            st.stop()
        
        if all_text.strip():
            st.success(f"Successfully extracted a total of {len(all_text.split())} words.")
            # Now, perform the conversion, which has its own progress bar.
            st.session_state.mp3_audio = convert_text_to_mp3_chunked(all_text, tld=selected_accent_tld)
            st.session_state.audio_filename = f"Berkshire_Hathaway_Letters_{start_year}-{end_year}.mp3"

elif input_method == "Upload a File":
    st.header("From an Uploaded File")
    st.markdown("Upload a `.pdf` or `.txt` file to convert it to an audiobook.")
    uploaded_file = st.file_uploader("Choose a file", type=['pdf', 'txt'])

    if uploaded_file is not None:
        if st.button("🚀 Generate Audiobook from File"):
            with st.spinner("Processing your document..."):
                # --- Step 1: Extract Text ---
                file_bytes = uploaded_file.getvalue()
                if uploaded_file.type == "application/pdf":
                    raw_text = extract_text_from_pdf_bytes(file_bytes)
                elif uploaded_file.type == "text/plain":
                    raw_text = extract_text_from_txt_bytes(file_bytes)
                else:
                    raw_text = "" # Should not happen due to file_uploader type constraints
                
                all_text = clean_and_prepare_text(raw_text)

                # Debug: Show the extracted text
                with st.expander("View Extracted Text (first 5000 chars)"):
                    st.text(all_text[:5000] + "..." if len(all_text) > 5000 else all_text)

                # --- Step 2 & 3: Show word count and convert ---
                if all_text.strip():
                    st.success(f"✅ Text successfully extracted! Found {len(all_text.split())} words.")
                    time.sleep(1) # A small delay to ensure the success message renders.
                    st.session_state.mp3_audio = convert_text_to_mp3_chunked(all_text, tld=selected_accent_tld)
                    st.session_state.audio_filename = f"{os.path.splitext(uploaded_file.name)[0]}.mp3"
                else:
                    st.error("Could not extract any text from the document. The file might be empty, be an image-only PDF, or have an unsupported format.")

elif input_method == "From a URL":
    st.header("From a URL")
    st.markdown("Enter the URL of a webpage or a direct link to a `.pdf` file.")
    url_input = st.text_input("Enter URL")

    if st.button("🚀 Generate Audiobook from URL") and url_input:
        if not url_input:
            st.warning("Please enter a URL.")
            st.stop()
        
        with st.spinner("Processing your document..."):
            try:
                # --- Step 1: Fetch and Extract Text ---
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                response = requests.get(url_input, headers=headers)
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').lower()

                if 'application/pdf' in content_type or url_input.lower().endswith('.pdf'):
                    raw_text = extract_text_from_pdf_bytes(response.content)
                else:
                    raw_text = extract_text_from_html(response.text)
                
                all_text = clean_and_prepare_text(raw_text)

                # Debug: Show the extracted text
                with st.expander("View Extracted Text (first 5000 chars)"):
                    st.text(all_text[:5000] + "..." if len(all_text) > 5000 else all_text)

                # --- Step 2 & 3: Show word count and convert ---
                if all_text.strip():
                    st.success(f"✅ Text successfully extracted! Found {len(all_text.split())} words.")
                    time.sleep(1) # A small delay to ensure the success message renders.
                    st.session_state.mp3_audio = convert_text_to_mp3_chunked(all_text, tld=selected_accent_tld)
                    st.session_state.audio_filename = "audiobook_from_url.mp3"
                else:
                    st.error("Could not extract any text from the document. The URL might point to an empty page, an image-only PDF, or have an unsupported format.")

            except requests.exceptions.RequestException as e:
                st.error(f"Failed to fetch or access URL: {e}")
            except Exception as e:
                st.error(f"An unexpected error occurred during processing: {e}")

# --- Final Step: Show Download Button if audio is ready ---
if st.session_state.mp3_audio is not None:
    st.success("Audiobook generation complete!")
    st.audio(st.session_state.mp3_audio, format='audio/mp3')
    
    st.info("Click the button below to save the MP3 file. Your browser will open a dialog asking you where to save it.")
    st.download_button(
        label="⬇️ Download Audiobook (MP3)",
        data=st.session_state.mp3_audio,
        file_name=st.session_state.audio_filename,
        mime='audio/mp3',
        use_container_width=True
    )
    # Clear the state so it doesn't reappear on a simple page refresh
    st.session_state.mp3_audio = None
    st.session_state.audio_filename = None