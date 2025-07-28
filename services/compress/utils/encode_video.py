'''
This script performs video encoding using various codecs, potentially optimized
for scene content type.
'''
import ffmpeg
import os
import re
import pandas as pd
import time
import sys
import traceback
import numpy as np
from analyze_video_fast import analyze_video_quality_metrics
from filter_recommendation import recommend_preprocessing_filters,should_apply_preprocessing, apply_preprocessing_filters
# Adjust sys.path to allow imports from the 'utils' directory
# This assumes check_hardware.py is in src/utilities/
# and other modules like encode_video.py are in utils/
UTILS_DIR = os.path.abspath(os.path.dirname(__file__))
if UTILS_DIR not in sys.path:
    sys.path.insert(0, UTILS_DIR)


# Import configurations
try:
    from encoder_configs import ENCODER_SETTINGS, SCENE_SPECIFIC_PARAMS, MODEL_CQ_REFERENCE_CODEC, QUALITY_MAPPING_ANCHORS
except ImportError:
    print("Error: Could not import configurations from 'encoder_configs.py'. Make sure the file exists in the 'src' directory.")
    # Define empty fallbacks or exit if critical
    ENCODER_SETTINGS = {}
    SCENE_SPECIFIC_PARAMS = {}
    MODEL_CQ_REFERENCE_CODEC = ""
    QUALITY_MAPPING_ANCHORS = {}
    # Consider sys.exit("Critical configuration file missing.")


def encode_video_with_smart_preprocessing(input_path, output_path, codec, cq, 
                                        scene_type=None, contrast_value=None,
                                        target_vmaf=93.0, temp_dir="./temp", 
                                        enable_preprocessing=True, time_budget=None,
                                        logging_enabled=True):
    """
    Enhanced encoding with intelligent preprocessing filter selection.
    """
    original_input = input_path
    
    if enable_preprocessing and scene_type:
        if logging_enabled:
            print("🔍 Analyzing video quality for preprocessing recommendations...")
        
        # Analyze video quality metrics
        quality_metrics = analyze_video_quality_metrics(input_path, num_frames=20)
        
        if quality_metrics:
            # Display analysis results
            if logging_enabled:
                print(f"📊 Quality Analysis Results:")
                print(f"   🔊 Noise Level: {quality_metrics['noise_level']:.2f}")
                print(f"   🔍 Sharpness: {quality_metrics['sharpness']:.2f}")
                print(f"   🎨 Contrast: {quality_metrics['contrast']:.2f}")
                print(f"   💡 Brightness: {quality_metrics['brightness']:.2f}")
                print(f"   🌈 Color Saturation: {quality_metrics['color_saturation']:.2f}")
                print(f"   ⚡ Motion Blur: {quality_metrics['motion_blur']:.2f}")
                print(f"   📦 Compression Artifacts: {quality_metrics['compression_artifacts']:.2f}")
                print(f"   📝 Text Content: {quality_metrics['text_content']:.2f}")
            
            # Get preprocessing recommendations
            decision = should_apply_preprocessing(quality_metrics, scene_type, target_vmaf, time_budget)
            
            if decision['apply_preprocessing']:
                filters_to_apply = decision['recommended_filters'] or decision['priority_filters']
                
                if filters_to_apply:
                    if logging_enabled:
                        print(f"✅ Applying {len(filters_to_apply)} preprocessing filters:")
                    recommendations = recommend_preprocessing_filters(quality_metrics, scene_type, target_vmaf)
                    
                    for i, filter_name in enumerate(filters_to_apply):
                        if i < len(recommendations['reasons']) and logging_enabled:
                            print(f"   • {filter_name} - {recommendations['reasons'][i]}")
                    
                    if logging_enabled:
                        print(f"📈 Expected VMAF gain: +{recommendations['expected_vmaf_gain']:.1f} points")
                        print(f"⏱️ Processing time increase: +{recommendations['processing_time_increase']:.0f}%")
                    
                    # Apply preprocessing
                    preprocessed_path = apply_preprocessing_filters(input_path, filters_to_apply, temp_dir)
                    if preprocessed_path != input_path:
                        input_path = preprocessed_path
                        if logging_enabled:
                            print("✅ Preprocessing completed successfully")
                    else:
                        if logging_enabled:
                            print("⚠️ Preprocessing failed, using original video")
                else:
                    if logging_enabled:
                        print("⚠️ No beneficial filters identified")
            else:
                if logging_enabled:
                    print("⏭️ Skipping preprocessing:")
                    for reason in decision['skip_reasons']:
                        print(f"   • {reason}")
        else:
            if logging_enabled:
                print("❌ Failed to analyze video quality, skipping preprocessing")
    
    # Proceed with original encoding function
    result = encode_video(input_path, output_path, codec, cq, scene_type=scene_type, 
                         contrast_value=contrast_value, logging_enabled=logging_enabled)
    
    # Cleanup preprocessed file if created
    if input_path != original_input and os.path.exists(input_path):
        os.remove(input_path)
    
    return result


def get_contrast_optimized_params(scene_type, contrast_value, codec):
    """
    Get contrast-optimized encoding parameters for a specific scene type and codec.
    
    Args:
        scene_type (str): The classified scene type
        contrast_value (float): The calculated perceptual contrast (0.0-1.0)
        codec (str): The codec being used
        
    Returns:
        dict: Additional encoding parameters optimized for contrast
    """
    # Start with empty params dictionary
    params = {}
    
    # Classify contrast into categories for easier parameter selection
    if contrast_value > 0.7:
        contrast_category = "high"
    elif contrast_value < 0.3:
        contrast_category = "low"
    else:
        contrast_category = "medium"
    
    # --- NVENC-based codecs (AV1, HEVC, H264) ---
    if codec in ["av1_nvenc", "hevc_nvenc", "h264_nvenc"]:      
        # Set spatial AQ strength based on contrast
        if contrast_category == "high":
            params['spatial-aq'] = 1
            params['aq-strength'] = 8  # Higher strength for high contrast (NVENC 1-15)
        elif contrast_category == "low":
            params['spatial-aq'] = 1
            params['aq-strength'] = 4  # Lower strength for low contrast
        else:
            params['spatial-aq'] = 1
            params['aq-strength'] = 6  # Medium strength
        
        # Adjust temporal AQ based on scene type and contrast
        if scene_type == 'Faces / People' and contrast_category == "high":
            params['temporal-aq'] = 1  # Enable temporal AQ for high contrast faces
        else:
            params['temporal-aq'] = 0  # Default
    
    # --- x264 ---
    elif codec == "H264" or "libx264" in codec:
        # AQ mode and strength adjustments
        if contrast_category == "high":
            params['aq-mode'] = 2  # Variance AQ with auto-variance
            params['aq-strength'] = 1.2  # Higher strength for high contrast
        elif contrast_category == "low":
            params['aq-mode'] = 1  # Standard AQ
            params['aq-strength'] = 0.8  # Lower strength for low contrast
        else:
            params['aq-mode'] = 1
            params['aq-strength'] = 1.0  # Default
    
    # --- x265 ---
    elif codec == "hevc" or "libx265" in codec:
        # AQ mode and strength adjustments
        if contrast_category == "high":
            params['aq-mode'] = 3  # Auto-variance AQ with more aggressive bias
            params['aq-strength'] = 1.2  # Higher strength
        elif contrast_category == "low":
            params['aq-mode'] = 2  # Standard AQ
            params['aq-strength'] = 0.8  # Lower strength
        else:
            params['aq-mode'] = 2
            params['aq-strength'] = 1.0  # Default
    
    # --- libaom-av1 ---
    elif "libaom-av1" in codec or codec == "av1_fallback":
        if contrast_category == "high":
            params['aq-mode'] = 2  # Variance-based adaptive quantization
            params['deltaq-mode'] = 3  # Perceptual deltaq mode
        elif contrast_category == "low":
            params['aq-mode'] = 1  # Default adaptive quantization
            params['deltaq-mode'] = 0  # Disabled
        else:
            params['aq-mode'] = 1
            params['deltaq-mode'] = 0
    
    # --- SVT-AV1 ---
    elif "libsvtav1" in codec or codec == "av1_optimized":
        # params for SVT-AV1
        if contrast_category == "high":
            params['aq-mode'] = 2
            params['enable-hdr'] = 1
        elif contrast_category == "low":
            params['aq-mode'] = 1
            params['enable-hdr'] = 0
        else:
            params['aq-mode'] = 1
            params['enable-hdr'] = 0
    
    # --- Scene-specific contrast adjustments ---
    # For text content, adjust parameters further based on contrast
    if scene_type == 'Screen Content / Text':
        if contrast_category == "high":
            params['sharpness'] = 0  # Preserve sharpness
            if "NVENC" in codec:
                params['rc-lookahead'] = 20  # More lookahead for complex text
        elif contrast_category == "low":
            params['sharpness'] = 1  # Some sharpening for low contrast text
            if "NVENC" in codec:
                params['rc-lookahead'] = 8  # Less lookahead needed
    
    # For faces, special handling based on contrast
    elif scene_type == 'Faces / People':
        if contrast_category == "high":
            if "NVENC" in codec:
                params['rc-lookahead'] = 20  # More lookahead for dramatic lighting
        else:
            if "NVENC" in codec:
                params['rc-lookahead'] = 15  # Standard lookahead
    
    return params

def encode_video(input_path, output_path, codec, rate=None, max_bit_rate=None, preset=None, scene_type=None, contrast_value=None, logging_enabled=True):
    """
    Encodes a video using specified codec settings, optimized for scene type and contrast.
    Audio is copied without re-encoding for efficiency.
    
    Note: encoding decisions should be made BEFORE calling this function.
    
    """
    
        
    if logging_enabled:
        print(f"Encoding video using codec: {codec}, scene: {scene_type}, model_predicted_rate: {rate}")
        if contrast_value is not None:
            print(f"Using contrast value: {contrast_value:.2f}")

    base_settings = ENCODER_SETTINGS.get(codec)
    if not base_settings:
        print(f"Error: Codec settings for '{codec}' not found in ENCODER_SETTINGS.")
        return None, None

    # --- Parameter Prioritization ---
    # 1. Start with base settings
    current_settings = base_settings.copy()


    # 2. Apply scene-specific overrides if scene_type is provided
    if scene_type:
        codec_scene_params = SCENE_SPECIFIC_PARAMS.get(codec, {})
        # Get params for the specific scene, fallback to 'other', then empty dict
        scene_params = codec_scene_params.get(scene_type, codec_scene_params.get('other', {}))
        if scene_params:
            if logging_enabled:
                print(f"Applying scene-specific params for '{scene_type}': {scene_params}")
            current_settings.update(scene_params) # Update base with scene specifics

    # 3. Apply contrast-specific overrides if contrast_value is provided
    if contrast_value is not None:
        contrast_params = get_contrast_optimized_params(scene_type, contrast_value, codec)
        if contrast_params:
            if logging_enabled:
                print(f"Applying contrast-specific params: {contrast_params}")
            current_settings.update(contrast_params)

    # 4. Apply function arguments as highest priority overrides
    # Apply 'rate' (which is the model_predicted_av1_cq)
    if rate is not None:
        model_predicted_ref_cq = int(rate) # This is the CQ from find_optimal_cq, on MODEL_CQ_REFERENCE_CODEC's scale

        if codec == MODEL_CQ_REFERENCE_CODEC:
            # Use the predicted CQ directly for the reference codec
            if 'cq' in current_settings: # Assuming reference codec uses 'cq'
                current_settings['cq'] = model_predicted_ref_cq
                if 'crf' in current_settings: del current_settings['crf']
                # Ensure rate control is set correctly for CQ (e.g., constqp for NVENC)
                if codec.endswith("_NVENC") or codec.endswith("_QSV") or 'videotoolbox' in codec: # Added videotoolbox
                    current_settings['rc'] = 'constqp' # Or appropriate for videotoolbox if it differs
                if logging_enabled: print(f"Applying model CQ directly for {MODEL_CQ_REFERENCE_CODEC}: {current_settings['cq']}")
            elif 'crf' in current_settings: # If reference codec uses CRF (less common for AV1 model output)
                current_settings['crf'] = model_predicted_ref_cq # Or a 1:1 mapping if scales are identical
                if 'cq' in current_settings: del current_settings['cq']
                if logging_enabled: print(f"Applying model CQ as CRF for {MODEL_CQ_REFERENCE_CODEC}: {current_settings['crf']}")
            else:
                if logging_enabled: print(f"Warning: Neither 'cq' nor 'crf' in base settings for {MODEL_CQ_REFERENCE_CODEC}. Applying rate as 'crf'.")
                current_settings['crf'] = model_predicted_ref_cq

        elif codec in QUALITY_MAPPING_ANCHORS:
            mapping_config = QUALITY_MAPPING_ANCHORS[codec]
            
            model_anchor_cqs = [p[0] for p in mapping_config['anchor_points']]
            target_anchor_params = [p[1] for p in mapping_config['anchor_points']]

            # Interpolate
            # Ensure model_predicted_ref_cq is within the model_ref_cq_range for stable interpolation
            clamped_model_cq = np.clip(model_predicted_ref_cq, mapping_config['model_ref_cq_range'][0], mapping_config['model_ref_cq_range'][1])
            if logging_enabled and clamped_model_cq != model_predicted_ref_cq:
                print(f"Clamped model_predicted_ref_cq from {model_predicted_ref_cq} to {clamped_model_cq} for interpolation based on model_ref_cq_range.")

            mapped_param_float = np.interp(clamped_model_cq, model_anchor_cqs, target_anchor_params)
            
            # Clamp to target parameter range
            min_target_param, max_target_param = mapping_config['target_param_range']
            mapped_param_clamped = np.clip(mapped_param_float, min_target_param, max_target_param)
            mapped_param_int = int(round(mapped_param_clamped))

            target_param_type = mapping_config['target_param_type']
            if target_param_type == 'cq':
                current_settings['cq'] = mapped_param_int
                if 'crf' in current_settings: del current_settings['crf']
                if codec.endswith("_NVENC") or codec.endswith("_QSV") or 'videotoolbox' in codec: # Added videotoolbox
                    current_settings['rc'] = 'constqp'
                if logging_enabled: print(f"Applying mapped CQ for {codec} from model ref CQ {model_predicted_ref_cq}: {current_settings['cq']}")
            elif target_param_type == 'crf':
                current_settings['crf'] = mapped_param_int
                if 'cq' in current_settings: del current_settings['cq']
                # Specific handling for codecs like libaom-av1 if they use CRF and need b:v=0
                if base_settings.get("codec") == "libaom-av1" and 'b:v' in current_settings:
                    current_settings['b:v'] = "0"
                if logging_enabled: print(f"Applying mapped CRF for {codec} from model ref CQ {model_predicted_ref_cq}: {current_settings['crf']}")
            else:
                if logging_enabled: print(f"Warning: Unknown target_param_type '{target_param_type}' for {codec}. Using direct rate as fallback.")
                if 'cq' in current_settings: current_settings['cq'] = model_predicted_ref_cq
                elif 'crf' in current_settings: current_settings['crf'] = model_predicted_ref_cq

        else:
            if logging_enabled:
                print(f"Warning: No quality mapping found for {codec} in QUALITY_MAPPING_ANCHORS. Applying model ref CQ {model_predicted_ref_cq} directly (may be inappropriate).")
            # Fallback: apply the rate directly, trying 'cq' then 'crf'
            if 'cq' in current_settings:
                 current_settings['cq'] = model_predicted_ref_cq
                 if 'crf' in current_settings: del current_settings['crf']
                 if codec.endswith("_NVENC") or codec.endswith("_QSV") or 'videotoolbox' in codec: current_settings['rc'] = 'constqp'
            elif 'crf' in current_settings:
                 current_settings['crf'] = model_predicted_ref_cq
                 if 'cq' in current_settings: del current_settings['cq']
                 if base_settings.get("codec") == "libaom-av1" and 'b:v' in current_settings: current_settings['b:v'] = "0"
            else: # Fallback: Assume CRF if neither is defined in base settings
                 current_settings['crf'] = model_predicted_ref_cq
            if logging_enabled: print(f"Applied direct rate {model_predicted_ref_cq} to {codec}.")

    # Apply 'preset' override if provided directly to function
    if preset is not None:
        current_settings['preset'] = preset
        if logging_enabled:
            print(f"Applying preset override: {preset}")

    # Apply 'max_bit_rate' override (and calculate bufsize)
    if max_bit_rate is not None:
        current_settings['maxrate'] = max_bit_rate
        try:
            # Extract numeric part and unit (k or m)
            numeric_maxrate = int(re.sub(r'\D', '', max_bit_rate))
            unit = re.sub(r'\d', '', max_bit_rate).lower()
            if unit not in ['k', 'm']: unit = 'k' # Default to k if unit is missing/invalid
            bufsize_val = numeric_maxrate * 2
            current_settings['bufsize'] = f"{bufsize_val}{unit}"
            # Ensure rate control mode supports maxrate (e.g., vbr for NVENC, not constqp)
            if (codec.endswith("_NVENC") or codec.endswith("_QSV")) and current_settings.get('rc') == 'constqp':
                 current_settings['rc'] = 'vbr' # Switch to VBR if maxrate is set
                 if logging_enabled:
                     print("Switched rate control to 'vbr' due to max_bit_rate setting.")
            if logging_enabled:
                print(f"Applying maxrate: {current_settings['maxrate']}, bufsize: {current_settings['bufsize']}")
        except ValueError:
            # Keep warning prints
            print(f"Warning: Could not parse max_bit_rate '{max_bit_rate}'. Ignoring.")
        except Exception as e:
             # Keep warning prints
             print(f"Warning: Error processing max_bit_rate '{max_bit_rate}': {e}. Ignoring.")


    # --- Build output_args dictionary for ffmpeg-python ---
    output_args = {}
    
    # Map internal setting names to actual ffmpeg argument names
    key_map = {
        'keyint': 'g',          # Keyframe interval
        'bitrate': 'b:v',       # Video bitrate (use if needed, usually CQ/CRF is preferred)
        'codec': 'vcodec'       # Ensure video codec is set correctly
        # Add other mappings if your internal names differ from ffmpeg's
    }

    # Add all current settings to output_args, applying key mapping
    for key, value in current_settings.items():
        ffmpeg_key = key_map.get(key, key) # Use mapped key or original key if not in map
        output_args[ffmpeg_key] = value

    # Ensure vcodec is explicitly set if it wasn't the primary 'codec' key
    if 'vcodec' not in output_args and 'codec' in current_settings:
         output_args['vcodec'] = current_settings['codec']

    # Remove the internal 'codec' key if it's different from 'vcodec' and still present
    if 'codec' in output_args and output_args.get('codec') != output_args.get('vcodec'):
         del output_args['codec']

    # **AUDIO HANDLING - Check if audio exists first**
    has_audio = check_audio_stream(input_path)
    
    if has_audio:
        output_args['acodec'] = 'copy'
        # Map video and audio streams separately - don't use a list
        output_args['map'] = '0'  # Map all streams, let FFmpeg handle it
    else:
        # No audio - only map video
        output_args['map'] = '0:v:0'  # Only video stream
    
    if logging_enabled:
        print(f"Audio detected: {has_audio}")
        print("Final FFmpeg output args:", output_args)

    # --- Execute FFmpeg ---
    try:
        start_time = time.time()
        log_level = 'info' if logging_enabled else 'error'
        
        # Create input and output with proper stream mapping
        input_stream = ffmpeg.input(input_path)
        output_stream = ffmpeg.output(input_stream, output_path, **output_args)
        
        result = output_stream.run(
            overwrite_output=True,
            capture_stdout=True,
            capture_stderr=True,
            quiet=(not logging_enabled)
        )
        
        end_time = time.time()
        encoding_time_calculated = round(end_time - start_time, 2)
        stderr = result[1].decode("utf-8") if result[1] else ""

        # Extract final encoding log line (optional, for debugging)
        encoding_results_log = None
        if logging_enabled: # Only parse log if logging is on
            for line in stderr.splitlines():
                if "time=" in line: # Find the last line reporting time/stats
                    encoding_results_log = line

        if logging_enabled:
            print(f"Successfully encoded using {codec} scene '{scene_type}': {output_path}")
        return encoding_results_log, encoding_time_calculated

    except ffmpeg.Error as e:
        print(f"Error encoding with {codec} scene '{scene_type}': {e}")
        print(f"FFmpeg stderr: {e.stderr.decode('utf8')}")
        return None, None
    except Exception as e:
        print(f"Unexpected error during encoding with {codec} scene '{scene_type}': {e}")
        traceback.print_exc()
        return None, None
    
def encode_lossless_video(input_path, output_path, logging_enabled=True):
    """
    Encode video using FFV1 lossless codec with optimized settings.
    This function is specifically for lossless encoding and doesn't use the main
    encode_video function to avoid codec mapping issues.
    """
    import subprocess
    import time
    
    if logging_enabled:
        print(f"🎥 Encoding lossless video with FFV1 codec...")
    
    # FFV1 lossless encoding command
    cmd = [
        'ffmpeg',
        '-i', input_path,
        '-c:v', 'ffv1',           # FFV1 codec
        '-level', '3',            # FFV1 level 3
        '-coder', '1',            # Range coder for better compression
        '-context', '1',          # Large context
        '-g', '1',                # GOP size 1 for lossless
        '-slices', '4',           # 4 slices for parallel processing
        '-slicecrc', '1',         # Enable slice CRC
        '-pix_fmt', 'yuv420p',    # Pixel format
        '-c:a', 'copy',           # Copy audio without re-encoding
        '-y',                     # Overwrite output file
        output_path
    ]
    
    try:
        start_time = time.time()
        
        if logging_enabled:
            print(f"🔧 FFmpeg command: {' '.join(cmd)}")
        
        # Run FFmpeg command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        end_time = time.time()
        encoding_time = round(end_time - start_time, 2)
        
        if logging_enabled:
            print(f"✅ FFV1 lossless encoding completed in {encoding_time:.1f}s")
        
        return "FFV1 lossless encoding successful", encoding_time
        
    except subprocess.CalledProcessError as e:
        if logging_enabled:
            print(f"❌ FFmpeg error: {e}")
            print(f"   stdout: {e.stdout}")
            print(f"   stderr: {e.stderr}")
        return None, None
        
    except Exception as e:
        if logging_enabled:
            print(f"❌ Unexpected error during lossless encoding: {e}")
        return None, None



def check_audio_stream(input_path):
    """Check if the input video has an audio stream."""
    try:
        import subprocess
        import json
        
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', 
            '-show_streams', input_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # Check if any stream is audio
        has_audio = any(stream.get('codec_type') == 'audio' for stream in data.get('streams', []))
        return has_audio
    except Exception as e:
        # If we can't detect, assume no audio to be safe
        return False

# Example usage (optional, for testing this script directly)
if __name__ == '__main__':
    utilities_path = os.path.join(os.path.dirname(__file__), 'utilities')
    if utilities_path not in sys.path:
        sys.path.append(utilities_path)
    try:
        from check_hardware import get_best_working_codec
    except ImportError:
        print("Error: Could not import 'get_best_working_codec' from 'utilities.check_hardware'. Make sure the file exists and is in the correct path.")
        # Define a dummy function if import fails, so the script can still attempt to run with a default
        def get_best_working_codec():
            print("Warning: 'get_best_working_codec' not found, defaulting to H264.")
            return "H264"


    # Create dummy input file for testing
    dummy_input = "dummy_input.mp4"
    if not os.path.exists(dummy_input):
        try:
            print("Creating dummy input file...")
            (
                ffmpeg
                .input('testsrc=duration=5:size=1280x720:rate=30', format='lavfi')
                .output(dummy_input, pix_fmt='yuv420p')
                .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
            )
            print("Dummy input created.")
        except ffmpeg.Error as e:
            print(f"Failed to create dummy input. FFmpeg stderr:")
            print(e.stderr.decode('utf8'))
            dummy_input = None # Ensure dummy_input is None if creation fails
        except Exception as e:
            print(f"Failed to create dummy input with an unexpected error: {e}")
            traceback.print_exc()
            dummy_input = None # Ensure dummy_input is None if creation fails

    if dummy_input and os.path.exists(dummy_input):
        print("\n--- Determining best available codec ---")
        # Use the get_best_working_codec function to select the codec
        # This function will test codecs in the order defined in check_hardware.py
        # (VideoToolbox, NVENC, then software encoders)
        selected_codec = get_best_working_codec()
        print(f"Selected codec by hardware check: {selected_codec}")

        print(f"\n--- Testing encode_video with selected codec: {selected_codec} ---")
        
        # Define a unique output file name based on the selected codec
        test_output_filename = f"test_output_{selected_codec.replace('/', '_')}.mp4" # Sanitize codec name for filename
        
        test_scene = "Gaming Content" # Example scene type
        test_rate = 30  # Example rate (CQ/CRF value for the MODEL_CQ_REFERENCE_CODEC scale)
        # You might want to adjust test_rate or not use it if the selected_codec
        # is not the MODEL_CQ_REFERENCE_CODEC and you haven't fine-tuned mappings for all.
        # For a simple "does it encode" test, you can omit 'rate' and let defaults apply.

        log, time_taken = encode_video(
            input_path=dummy_input,
            output_path=test_output_filename,
            codec=selected_codec,
            rate=test_rate, # Pass the rate to test the mapping logic
            scene_type=test_scene,
            logging_enabled=True # Keep logging enabled for detailed output
        )

        if log is not None or (os.path.exists(test_output_filename) and os.path.getsize(test_output_filename) > 0) : # Check if file exists and is not empty as a fallback
            print(f"\nTest encode successful for {selected_codec} ({test_scene})!")
            print(f"Time taken: {time_taken} seconds")
            if log:
                print(f"Log line: {log}")
            if os.path.exists(test_output_filename):
                print(f"Output file created: {test_output_filename} (Size: {os.path.getsize(test_output_filename)} bytes)")
                # print(f"Consider removing test file: os.remove('{test_output_filename}')")
            # os.remove(test_output_filename) # Clean up test file if desired
        else:
            print(f"\nTest encode failed for {selected_codec} ({test_scene}).")

        # Clean up dummy input
        # print(f"Consider removing dummy input: os.remove('{dummy_input}')")
        # os.remove(dummy_input)
    else:
        print("Skipping test encode as dummy input could not be created or found.")