# NOTE: this is _not_ the original code of IDM!
# As such, while it is close and seems to function well,
# its performance might be bit off from what is reported
# in the paper.

from argparse import ArgumentParser
import pickle
import cv2
import numpy as np
import json
import torch as th
import torch
from agent import ENV_KWARGS
from inverse_dynamics_model import IDMAgent


KEYBOARD_BUTTON_MAPPING = {
    "key.keyboard.escape" :"ESC",
    "key.keyboard.s" :"back",
    "key.keyboard.q" :"drop",
    "key.keyboard.w" :"forward",
    "key.keyboard.1" :"hotbar.1",
    "key.keyboard.2" :"hotbar.2",
    "key.keyboard.3" :"hotbar.3",
    "key.keyboard.4" :"hotbar.4",
    "key.keyboard.5" :"hotbar.5",
    "key.keyboard.6" :"hotbar.6",
    "key.keyboard.7" :"hotbar.7",
    "key.keyboard.8" :"hotbar.8",
    "key.keyboard.9" :"hotbar.9",
    "key.keyboard.e" :"inventory",
    "key.keyboard.space" :"jump",
    "key.keyboard.a" :"left",
    "key.keyboard.d" :"right",
    "key.keyboard.left.shift" :"sneak",
    "key.keyboard.left.control" :"sprint",
    "key.keyboard.f" :"swapHands",
}

# Template action
NOOP_ACTION = {
    "ESC": 0,
    "back": 0,
    "drop": 0,
    "forward": 0,
    "hotbar.1": 0,
    "hotbar.2": 0,
    "hotbar.3": 0,
    "hotbar.4": 0,
    "hotbar.5": 0,
    "hotbar.6": 0,
    "hotbar.7": 0,
    "hotbar.8": 0,
    "hotbar.9": 0,
    "inventory": 0,
    "jump": 0,
    "left": 0,
    "right": 0,
    "sneak": 0,
    "sprint": 0,
    "swapHands": 0,
    "camera": np.array([0, 0]),
    "attack": 0,
    "use": 0,
    "pickItem": 0,
}


used_buttons=["attack",
   "back",
    "forward",
    "jump",
    "left",
    "right",
    "sneak",
    "sprint",
    "use",
    "drop",
    "inventory",
    "hotbar.1",
    "hotbar.2",
    "hotbar.3",
    "hotbar.4",
    "hotbar.5",
    "hotbar.6",
    "hotbar.7",
    "hotbar.8",
    "hotbar.9"]

camera_bins=[-10,
             -6,
             -3,
             -2,
             -1,
             0,
             1,
             2,
             3,
             6,
             10
             ]


MESSAGE = """
This script will take a video, predict actions for its frames and
and show them with a cv2 window.

Press any button the window to proceed to the next frame.
"""

# Matches a number in the MineRL Java code regarding sensitivity
# This is for mapping from recorded sensitivity to the one used in the model
CAMERA_SCALER = 0.5 * 360.0 / 2400.0 # Needs calibration for sure



agent_settings = {'version': 1361,
                  'model': {'args': {
                      'net': {'args': {'attention_heads': 32, 
                                       'attention_mask_style': 'none',
                                       'attention_memory_size': 128,
                                       'conv3d_params': {'inchan': 3, 'kernel_size': [5, 1, 1], 'outchan': 128, 'padding': [2, 0, 0]},
                                       'hidsize': 4096,
                                       'img_shape': [128, 128, 128],
                                       'impala_kwargs': {'post_pool_groups': 1},
                                       'impala_width': 4,
                                       'init_norm_kwargs': {'batch_norm': False, 'group_norm_groups': 1},
                                       'n_recurrence_layers': 2,
                                       'only_img_input': True,
                                       'pointwise_ratio': 4,
                                       'pointwise_use_activation': False,
                                       'recurrence_is_residual': True,
                                       'recurrence_type': 'transformer',
                                       'single_output': True,
                                       'timesteps': 16,
                                       'use_pointwise_layer': True,
                                       'use_pre_lstm_ln': False},
                              'function': 'ypt.model.inverse_action_model:InverseActionNet',
                              'local_args': {'hidsize': 128, 'impala_width': 1}},
                      'pi_head_opts': {'temperature': 1}},
                      'function': 'ypt.model.inverse_action_model:create'
                      },

                  }

def json_action_to_env_action(json_action):
    """
    Converts a json action into a MineRL action.
    Returns (minerl_action, is_null_action)
    """
    # This might be slow...
    env_action = NOOP_ACTION.copy()
    # As a safeguard, make camera action again so we do not override anything
    env_action["camera"] = np.array([0, 0])

    is_null_action = True
    keyboard_keys = json_action["keyboard"]["keys"]
    for key in keyboard_keys:
        # You can have keys that we do not use, so just skip them
        # NOTE in original training code, ESC was removed and replaced with
        #      "inventory" action if GUI was open.
        #      Not doing it here, as BASALT uses ESC to quit the game.
        if key in KEYBOARD_BUTTON_MAPPING:
            env_action[KEYBOARD_BUTTON_MAPPING[key]] = 1
            is_null_action = False

    mouse = json_action["mouse"]
    camera_action = env_action["camera"]
    camera_action[0] = mouse["dy"] * CAMERA_SCALER
    camera_action[1] = mouse["dx"] * CAMERA_SCALER

    if mouse["dx"] != 0 or mouse["dy"] != 0:
        is_null_action = False
    else:
        if abs(camera_action[0]) > 180:
            camera_action[0] = 0
        if abs(camera_action[1]) > 180:
            camera_action[1] = 0

    mouse_buttons = mouse["buttons"]
    if 0 in mouse_buttons:
        env_action["attack"] = 1
        is_null_action = False
    if 1 in mouse_buttons:
        env_action["use"] = 1
        is_null_action = False
    if 2 in mouse_buttons:
        env_action["pickItem"] = 1
        is_null_action = False

    return env_action, is_null_action

def find_closes_camera_value(value):
    
    array = np.array(camera_bins)
    difference_array = np.absolute(array-value)
    index = difference_array.argmin()
    return index

def recorded_actions_to_torch(recorded_actions):
    camera = []
    buttons = []
    
    first=True
    for frame in recorded_actions:
        frame_buttons=[0]*len(used_buttons)
        for key in frame.keys():
            if key == "camera":
                first_bin = find_closes_camera_value(list(frame[key])[0])
                second_bin = find_closes_camera_value(list(frame[key])[1])
                camera.append([first_bin,second_bin])
            else:
                if key in used_buttons:
                    frame_buttons[used_buttons.index(key)] = frame[key]
        #     if first==True:
        #         print("key",key)
        # if first ==True:
        #     # print("frame_buttons",frame_buttons)
        #     first=False
        buttons.append(frame_buttons)
    camera=torch.tensor(camera)
    buttons=torch.tensor(buttons)
    return camera, buttons
    



def main(model, weights, video_path, json_path, n_batches, n_frames, accumulation):
    print(MESSAGE)
    if model == "":
        agent_parameters = agent_settings
    else:
        agent_parameters = pickle.load(open(model, "rb"))
    net_kwargs = agent_parameters["model"]["args"]["net"]["args"]
    pi_head_kwargs = agent_parameters["model"]["args"]["pi_head_opts"]
    pi_head_kwargs["temperature"] = float(pi_head_kwargs["temperature"])
    agent = IDMAgent(idm_net_kwargs=net_kwargs, pi_head_kwargs=pi_head_kwargs)
    
    if weights != "":
        agent.load_weights(weights)

    required_resolution = ENV_KWARGS["resolution"]
    cap = cv2.VideoCapture(video_path)

    json_index = 0
    with open(json_path) as json_file:
        json_lines = json_file.readlines()
        json_data = "[" + ",".join(json_lines) + "]"
        json_data = json.loads(json_data)
        
        
        
        
    """ Defining training objects """
    LEARNING_RATE = 0.000181
    WEIGHT_DECAY = 0.039428
    MAX_GRAD_NORM = 5.0
    
    trainable_parameters = agent.policy.parameters()
    
    optimizer = th.optim.Adam(
    trainable_parameters,
    lr=LEARNING_RATE,
    weight_decay=WEIGHT_DECAY
    )
    
    loss_func = th.nn.CrossEntropyLoss()

    for step in range(n_batches):
        th.cuda.empty_cache()
        # print("=== Loading up frames ===")
        frames = []
        recorded_actions = []
        for _ in range(n_frames):
            ret, frame = cap.read()
            if not ret:
                break
            assert frame.shape[0] == required_resolution[1] and frame.shape[1] == required_resolution[0], "Video must be of resolution {}".format(required_resolution)
            # BGR -> RGB
            frames.append(frame[..., ::-1])
            env_action, _ = json_action_to_env_action(json_data[json_index])
            recorded_actions.append(env_action)
            json_index += 1
        frames = np.stack(frames)
        
        
        # print("=== Predicting actions ===")
        pi_distribution = agent.predict_actions_training(frames)
        
        pi_camera=pi_distribution["camera"][0]
        pi_buttons=pi_distribution["buttons"][0]

        
        
        
        
        
        camera, buttons = recorded_actions_to_torch(recorded_actions)
        if False:# _ == 0:
            print("pi_distribution",pi_distribution)
            print("pi_distribution camera shape",pi_distribution["camera"].shape)
            print("pi_distribution buttons shape",pi_distribution["buttons"].shape)
            print("\n\nrecorded_actions",recorded_actions)
            print("\n\ncamera",camera)
            print("\n\nbuttons",buttons)
            print("\n\ncamera shape",camera.shape)
            print("\n\nbuttons shape",buttons.shape)
            
            
        loss = 0
        for i in range(n_frames):
            try:
                camera_loss = loss_func(pi_camera[i], camera[i])
                buttons_loss = loss_func(pi_buttons[i], buttons[i])*10
                loss = loss + camera_loss + buttons_loss
            except:
                print("ERROR 3")
                print("pi_camera[i]",pi_camera[i])
                print("camera[i]",camera[i])
                print("camera_loss",camera_loss)
                
                print("pi_buttons[i]",pi_buttons[i])
                print("buttons[i]",buttons[i])
                print("buttons_loss",buttons_loss)
            
            if i == 0 and step % accumulation == 0:
                print("pi_camera[i]",pi_camera[i])
                print("camera[i]",camera[i])
                print("camera_loss",camera_loss)
                
                print("pi_buttons[i]",pi_buttons[i])
                print("buttons[i]",buttons[i])
                print("buttons_loss",buttons_loss)
        print("Step:",step,end=" - ")
        print("Total loss",loss)
        loss.backward()
        agent.reset()
        #th.nn.utils.clip_grad_norm_(trainable_parameters, MAX_GRAD_NORM) #Applies gradient clipping
        if (step+1) % accumulation == 0:
            print("Optimizer step")
            optimizer.step()
            optimizer.zero_grad()


if __name__ == "__main__":
    parser = ArgumentParser("Run IDM on MineRL recordings.")

    parser.add_argument("--weights", type=str, default="", required=False, help="Path to the '.weights' file to be loaded.")
    parser.add_argument("--model", type=str, default="", required=False, help="Path to the '.model' file to be loaded.")
    parser.add_argument("--video-path", type=str, required=True, help="Path to a .mp4 file (Minecraft recording).")
    parser.add_argument("--jsonl-path", type=str, required=True, help="Path to a .jsonl file (Minecraft recording).")
    parser.add_argument("--n-frames", type=int, default=16, help="Number of frames to process at a time.")
    parser.add_argument("--n-batches", type=int, default=10, help="Number of batches (n-frames) to process for visualization.")
    parser.add_argument("--batch-accumulaton", type=int, default=10, help="Number of batches to process before optimizer step.")

    args = parser.parse_args()

    main(args.model, args.weights, args.video_path, args.jsonl_path, args.n_batches, args.n_frames, args.batch_accumulaton)
