import os
import random
import cloudinary
import cloudinary.api
import cloudinary.uploader # For uploading the merged video
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips
import requests # For downloading from Cloudinary and posting to Facebook
import tempfile # For managing temporary files and directories
import shutil   # For deleting the temporary directory
import json     # For the posted_media_tracker.json file
import sys      # To exit the script on critical errors

# --- Cloudinary Configuration ---
# These are loaded from GitHub Secrets (environment variables)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# --- Facebook Page Details (Loaded from GitHub Secrets) ---
PAGE_ID = os.getenv("PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

# --- Cloudinary Folder Names ---
# Source folder for random videos on Cloudinary
CLOUDINARY_SOURCE_VIDEO_FOLDER = "Quotes_Videos"
# Source folder for random background music on Cloudinary
CLOUDINARY_SOURCE_MUSIC_FOLDER = "backmusic"
# New folder on Cloudinary where the merged videos will be uploaded
CLOUDINARY_MERGED_VIDEO_FOLDER = "Merged_Posts"

# --- Local Tracking File ---
# This file tracks which source videos have already been posted to Facebook
POSTED_MEDIA_TRACKER = "posted_media_tracker.json"

# --- Supported File Extensions ---
SUPPORTED_VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv')
SUPPORTED_AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg', '.aac', '.flac')

def get_posted_media():
    """Loads the list of previously posted media details from the tracker file."""
    if os.path.exists(POSTED_MEDIA_TRACKER):
        with open(POSTED_MEDIA_TRACKER, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # Handle case where file is empty or corrupted JSON
                print(f"Warning: '{POSTED_MEDIA_TRACKER}' is empty or corrupted. Starting with an empty tracker.")
                return []
    return []

def save_posted_media(video_url, audio_url, merged_cloudinary_url):
    """Saves the details of a newly posted merged video to the tracker file."""
    posted_media = get_posted_media()
    posted_media.append({
        # Generate a unique ID for this post attempt (useful for debugging)
        "timestamp": os.getenv("GITHUB_RUN_ID", "local") + "_" + str(os.getenv("GITHUB_RUN_ATTEMPT", "0")) + "_" + str(os.getenv("GITHUB_JOB", "default_job")),
        "source_video_url": video_url,
        "source_audio_url": audio_url,
        "merged_cloudinary_url": merged_cloudinary_url
    })
    with open(POSTED_MEDIA_TRACKER, 'w') as f:
        json.dump(posted_media, f, indent=4)

def get_random_media_url(folder_name, resource_type, supported_extensions):
    """
    Fetches a random media URL from a specified Cloudinary folder,
    ensuring it's a supported type and, for source videos, hasn't been used before.
    """
    try:
        # Fetch up to 500 resources. Adjust max_results if you have more files.
        resources = cloudinary.api.resources(
            type="upload", 
            prefix=f"{folder_name}/", 
            resource_type=resource_type,
            max_results=500
        )['resources']

        if not resources:
            print(f"Error: No {resource_type} files found in Cloudinary folder '{folder_name}'.")
            return None

        all_urls = []
        for res in resources:
            # Extract extension from secure_url for reliable filtering
            url_path = res['secure_url'].split('?')[0] # Remove query parameters if any
            ext = os.path.splitext(url_path)[1].lower()
            if ext in supported_extensions:
                all_urls.append(res['secure_url'])
        
        if not all_urls:
            print(f"Warning: No supported {resource_type} files found in Cloudinary folder '{folder_name}' after filtering by URL extension.")
            return None

        # Logic to prevent re-using source videos.
        # This prevents the *same source video* from being used again in a merge.
        if folder_name == CLOUDINARY_SOURCE_VIDEO_FOLDER:
            posted_media = get_posted_media()
            posted_source_video_urls = [item['source_video_url'] for item in posted_media]
            unposted_source_urls = [url for url in all_urls if url not in posted_source_video_urls]
            
            if not unposted_source_urls:
                print(f"All unique source videos from '{CLOUDINARY_SOURCE_VIDEO_FOLDER}' have been used. Consider adding new videos or manually clearing '{POSTED_MEDIA_TRACKER}'.")
                return None
            return random.choice(unposted_source_urls)
        
        # For music (or other types), allow reuse
        return random.choice(all_urls)
    
    except Exception as e:
        print(f"Error fetching media from Cloudinary folder '{folder_name}': {e}")
        return None

def download_file(url, local_path):
    """Downloads a file from a given URL and saves it to a local path."""
    try:
        print(f"Downloading from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status() # Raise an exception for HTTP errors
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Saved to: {local_path}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading file from {url}: {e}")
        return False

def merge_video_with_audio(video_path, audio_path, output_folder):
    """
    Merges an audio file with a video file (muting original video sound)
    and saves the combined video to the output_folder.
    Returns the path to the merged file AND the clean base name of the original video.
    """
    # Ensure the output directory exists
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder, exist_ok=True)
        print(f"Created output directory: {output_folder}")

    try:
        print(f"\nLoading video: {os.path.basename(video_path)}")
        video_clip = VideoFileClip(video_path)
        
        print(f"Loading audio: {os.path.basename(audio_path)}")
        audio_clip = AudioFileClip(audio_path)

        # Adjust audio duration to match video duration
        if audio_clip.duration > video_clip.duration:
            print(f"  Warning: Audio ({audio_clip.duration:.2f}s) is longer than video ({video_clip.duration:.2f}s). Trimming audio.")
            audio_clip = audio_clip.subclip(0, video_clip.duration)
        elif audio_clip.duration < video_clip.duration:
            print(f"  Warning: Audio ({audio_clip.duration:.2f}s) is shorter than video ({video_clip.duration:.2f}s). Looping audio.")
            # Loop the audio to fill the video duration
            num_loops = int(video_clip.duration / audio_clip.duration) + 1
            looped_audio_segments = [audio_clip] * num_loops
            audio_clip = concatenate_videoclips(looped_audio_segments).subclip(0, video_clip.duration)

        # Set the video's audio to the new audio clip (this mutes the original video sound)
        final_clip = video_clip.set_audio(audio_clip)

        # Generate output filename. Clean it up for file system compatibility.
        original_video_basename = os.path.splitext(os.path.basename(video_path))[0]
        # Remove common appended IDs if present (e.g., -123456789.mp4 part)
        clean_original_video_name = re.sub(r'-\d+$', '', original_video_basename)
        
        # Further clean for general text use in title
        clean_original_video_name = clean_original_video_name.replace('_', ' ').replace('—', '-')
        
        # Ensure it doesn't end with a space after cleaning
        clean_original_video_name = clean_original_video_name.strip() 

        # Create filename for the merged output
        output_filename = f"merged_{clean_original_video_name.replace(' ', '_')}.mp4" # Replace spaces with underscores for filename
        output_path = os.path.join(output_folder, output_filename)

        print(f"Writing final video to: '{output_path}'")
        # Use common codecs for wide compatibility
        final_clip.write_videofile(output_path, codec="libx264", audio_codec="aac")

        # Close all clips to free up system resources
        video_clip.close()
        audio_clip.close()
        final_clip.close()

        print(f"\nSuccessfully merged and saved locally: '{output_filename}'")
        return output_path, clean_original_video_name # Return both path and clean name

    except Exception as e:
        print(f"Error during video/audio merging: {e}")
        return None, None

def upload_merged_video_to_cloudinary(file_path):
    """Uploads the locally merged video to Cloudinary."""
    try:
        print(f"Attempting to upload merged video: '{os.path.basename(file_path)}' to Cloudinary folder '{CLOUDINARY_MERGED_VIDEO_FOLDER}'...")
        upload_result = cloudinary.uploader.upload(
            file_path,
            folder=CLOUDINARY_MERGED_VIDEO_FOLDER, # Upload to the designated merged videos folder
            resource_type="video",                 # Explicitly specify video resource type
            use_filename=True,                     # Use the local filename on Cloudinary
            unique_filename=False,                 # Do not add unique suffix if filename already exists
            overwrite=False                        # Do not overwrite if a file with the same name exists
        )
        print(f"Successfully uploaded merged video. Secure URL: {upload_result['secure_url']}")
        return upload_result['secure_url']
    except Exception as e:
        print(f"Error uploading merged video to Cloudinary: {e}")
        return None

def post_video_to_facebook(video_url, post_title):
    """
    Posts a video from a Cloudinary URL to the Facebook page.
    Checks if this specific merged video URL has already been posted.
    """
    if not PAGE_ID or not FB_ACCESS_TOKEN:
        print("Error: Facebook PAGE_ID or FB_ACCESS_TOKEN not set. Cannot post to Facebook.")
        return False

    # --- NEW CHECK: Prevent re-posting the same merged video URL ---
    posted_media = get_posted_media()
    already_posted_merged_urls = [item['merged_cloudinary_url'] for item in posted_media if 'merged_cloudinary_url' in item]
    
    if video_url in already_posted_merged_urls:
        print(f"Warning: Merged video '{video_url}' has already been posted to Facebook. Skipping.")
        return True # Return True as it's considered "posted" (already done)
    # --- END NEW CHECK ---

    # Construct the full Facebook post message
    # Removed specific hashtags from here as they are included in post_title
    facebook_post_message = f"{post_title}" 

    # Use the /videos endpoint for posting videos hosted externally
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/videos"
    params = {
        "file_url": video_url, # Key parameter for external video URL
        "description": facebook_post_message, # Use the dynamic message
        "access_token": FB_ACCESS_TOKEN,
        "privacy": '{"value":"EVERYONE"}' # Make the post publicly visible
    }

    print(f"\nAttempting to post video to Facebook Page ID: {PAGE_ID}")
    print(f"Video URL for Facebook: {video_url}")
    print(f"Facebook Post Message: {facebook_post_message}")

    try:
        response = requests.post(url, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors (e.g., 4xx or 5xx)
        post_response = response.json()
        print("Video successfully posted to Facebook!")
        print(post_response) # Print Facebook's response for verification (e.g., post ID)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error posting video to Facebook: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response content: {e.response.text}") # Show full error response from Facebook
        return False

if __name__ == "__main__":
    print("--- Starting Automated Media Process for Facebook Post ---")

    # Create a temporary directory for all working files (downloads, merged output)
    temp_dir = tempfile.mkdtemp()
    print(f"Temporary working directory created: {temp_dir}")

    # Initialize variables to track paths and URLs throughout the process
    downloaded_source_video_url = None
    downloaded_source_audio_url = None
    merged_local_file_path = None
    clean_video_title = None # To store the title extracted from video name
    final_merged_cloudinary_url = None

    try:
        # 1. Get a random source video URL from Cloudinary (avoiding previously posted ones)
        downloaded_source_video_url = get_random_media_url(CLOUDINARY_SOURCE_VIDEO_FOLDER, "video", SUPPORTED_VIDEO_EXTENSIONS)
        if not downloaded_source_video_url:
            print("Could not get a valid source video URL from Cloudinary. Aborting.")
            sys.exit(1)

        # 2. Get a random background music URL from Cloudinary
        downloaded_source_audio_url = get_random_media_url(CLOUDINARY_SOURCE_MUSIC_FOLDER, "video", SUPPORTED_AUDIO_EXTENSIONS)
        if not downloaded_source_audio_url:
            print("Could not get a valid background music URL from Cloudinary. Aborting.")
            sys.exit(1)

        # Determine file extensions for temporary download filenames
        source_video_ext = os.path.splitext(downloaded_source_video_url.split('/')[-1].split('?')[0])[1].lower() or ".mp4"
        source_audio_ext = os.path.splitext(downloaded_source_audio_url.split('/')[-1].split('?')[0])[1].lower() or ".mp3"

        # 3. Download source video and audio to the temporary directory
        local_source_video_path = os.path.join(temp_dir, f"temp_source_video{source_video_ext}")
        local_source_audio_path = os.path.join(temp_dir, f"temp_source_audio{source_audio_ext}")

        if not download_file(downloaded_source_video_url, local_source_video_path):
            print("Failed to download source video. Aborting.")
            sys.exit(1)
        if not download_file(downloaded_source_audio_url, local_source_audio_path):
            print("Failed to download background audio. Aborting.")
            sys.exit(1)

        # 4. Merge the video and audio locally in the temporary directory
        merged_local_file_path, clean_video_title = merge_video_with_audio(local_source_video_path, local_source_audio_path, temp_dir)
        if not merged_local_file_path or not clean_video_title:
            print("Video merging failed. Aborting.")
            sys.exit(1)

        # 5. Upload the locally merged video to Cloudinary
        final_merged_cloudinary_url = upload_merged_video_to_cloudinary(merged_local_file_path)
        if not final_merged_cloudinary_url:
            print("Failed to upload merged video to Cloudinary. Aborting.")
            sys.exit(1)

        # 6. Construct the Facebook post message
        # Format: "Video Title #quotes #theunveiledtruth"
        facebook_post_message = f"{clean_video_title} #quotes #theunveiledtruth"

        # 7. Post the merged video (from Cloudinary) to the Facebook Page
        if post_video_to_facebook(final_merged_cloudinary_url, facebook_post_message):
            print("\nVideo successfully posted to Facebook!")
            # 8. Save tracking information for the posted video
            # Only save after successful post to avoid logging failed attempts
            save_posted_media(downloaded_source_video_url, downloaded_source_audio_url, final_merged_cloudinary_url)
            print(f"Posted media tracked in '{POSTED_MEDIA_TRACKER}'.")
        else:
            print("\nFailed to post video to Facebook. Aborting.")
            sys.exit(1)

    except Exception as main_process_error:
        print(f"\nAn unhandled error occurred in the main process: {main_process_error}")
        sys.exit(1) # Exit with a non-zero status to indicate failure

    finally:
        # 9. Clean up the temporary directory regardless of success or failure
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                print(f"\nCleaned up temporary directory: {temp_dir}")
            except OSError as e:
                print(f"Error removing temporary directory '{temp_dir}': {e}")
        print("\n--- Automated Process Finished ---")
