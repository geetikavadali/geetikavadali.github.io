---
layout: post
title: "What I have been working on / summer of '24"
date: 2024-07-22 02:14:47
description: "conferences, hospitals, ethernets and libraries / heat warnings and snow falls"
thumbnail: "https://substackcdn.com/image/fetch/f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F2ce33b44-b588-45b8-98a1-7ead0aef84a9_900x1600.jpeg"
tags:
  - substack
---

I have been very tardy in writing a third post haha. Bear with me though, I have a good enough reason. I find myself currently in a cafe drinking some mango sherbet. Its 33 degrees celsius in one of those towns where it dips to -40 in winters and you can see northern lights at the right place and time. They say its climate change. It crossed 53 degrees in Delhi too. That’s climate change too. I saw a post yesterday that said scientists already invented a glass type material that can be used to make buildings and generate energy simultaneously. And a comment below that was - “great wow, can’t wait to never hear about it ever again”. The way I chuckled and realised that that’s so true. Like imagine funding research for renewable sources of energy and still AL-ing oil, petroleum and coal companies. Why is it so difficult agh?

<figure class="substack-figure">
  <img src="https://substackcdn.com/image/fetch/$s_!_ksI!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Fd15c19d3-a789-423a-956b-b84b6c536158_948x261.png" width="948" height="261" loading="lazy" data-zoomable />
</figure>

Unfortunately for me, environment is not something I have been actively thinking or working on. I have been in Canada for 2 months now. I have 1 month more. I am interning at a university here. The research has been around ultrasound as point-of-care to when a person walks into hospital emergency with a broken shoulder.

Canadian healthcare is free. This unfortunately also means that it is prone to a lot of delays. Let’s assume we all have a common friend Mike who has suffered trauma on his shoulder. He cannot move it, its paniful and he really misses swimming. He takes a consultation, doctor suspects that Mike has a rotator cuff tear and prescribes him to get an MRI scan. Rotator cuff tear means a torn shoulder tendon and MRI is medical gold standard to diagnose these tears. Mike takes an appointment for scan but he is scheduled for 10 weeks from now. Almost 2.5 months. To live with such a pain all this time is definitely not ideal.

MRI infrastructure is costly, resource-intensive, not accessible to everyone and hence prone to very long wait times. What if we use a simpler and dumber imaging paradigm then? My research proposes ultrasound imaging using a handheld probe that comes with its own tablet. This is portable, accessible and can even be used by lightly trained sonographers.

<figure class="substack-figure">
  <img src="https://substackcdn.com/image/fetch/$s_!B3qQ!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Fd92e4112-64de-4d49-af41-ab0a9ae1c87d_960x540.jpeg" width="960" height="540" loading="lazy" data-zoomable />
</figure>

So at this point, we make Mike get his ultrasound “sweep” done. This sweep is like a long video of about 200-300 frames where each frame shows an ultrasound image at a specific angle of Mike’s shoulder. Ultrasound though has some problems itself. Although it is almost instant, it has a lot of noise. Most of these 200-300 frames are irrelevant and carry no important information about presence of tear or not. We want to be able to reduce this long long video to contain only the **most diagnostically important few frames.** For this, I have been working on a Deep Reinforcement Learning algorithm that picks the key frames/summary like a human radiologist would have.

The RL agent takes a decision at every frame to choose which frames will be included in the summary. If it makes a good decision and a good representative summary is created, the agent is rewarded. This way it learns what is expected of it. But if it makes a bad summary, it is penalised. This reward and penalty is a function of the next step of our pipeline - the deep learning classifer. This classifier is tasked to predict the presence of tear in Mike’s shoulder scan. Its confidence on its own prediction is key to judge how well the RL+classifier work.

Eventually when the classifier is fairly confident about its prediction, a report pops up on the sonographer’s tablet. This could be something like - ‘Our model is 94% confident that patient has a rotator-cuff tear’ (or something more professional). This is then communicated to Mike and his doctor. The doctor then can start targeted treatmet that came from this preliminary diagnosis and hopefully Mike then would be swimming way before his MRI appointment.

Do not judge the story and lack of all technical jargon. This had been my 3 minute thesis presentation infront people from various different backgrounds and I consider it a good low-level introduction to what I have been working on. I could use jargon but I’ll be doing that a little later.

<figure class="substack-figure">
  <img src="https://substackcdn.com/image/fetch/$s_!Yeot!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Fed9dd832-df5a-4c4b-99bd-fb39c34d416c_1844x1844.jpeg" width="1456" height="1456" loading="lazy" data-zoomable />
</figure>

Besides this sweet deal, I have been attending some amazing sessions on Continual Learning and similar stuff. My first week here was attending this very cool conference called Upper Bound. Couldnt have had a better introduction to academia here than that. Debating about RL in a room full of the best RL researchers. Rich Sutton, Doina Precup, Csaba Szepesvari, Martha and Adam White, Marlos Machado - all amazing. I also networked a bit here and there, got to know about the culture here and traveled through mountains and badlands and lakes and farmers’ markets. All have made it a nice experience. I wouldn’t lie, I dont think this could have been my best attempt at documenting my experience here. Maybe when I can, I’ll write up another one. But till then, this goes up and stays.

<figure class="substack-figure substack-portrait">
  <img src="https://substackcdn.com/image/fetch/$s_!ejOX!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F2ce33b44-b588-45b8-98a1-7ead0aef84a9_900x1600.jpeg" width="583" height="1036" loading="lazy" data-zoomable />
</figure>

PS - And in case you worry why the subtitle contains “hospitals”, the nearest train station to me is the Health sciences station and I have to cross the hospital from end to end internally to get there. So I was in hospitals more often than a normal person would. Plus, a labmate who did ultrasound scans took us to a cast clinic to show how he does the scan, and where our application eventually works. So that’s about it. I have been pretty healthy don’t worry.
