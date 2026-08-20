// ==UserScript==
// @name         抖音章節標記跳轉 + 關注
// @namespace    steven.controller-media
// @version      1.1
// @description  抖音播放器沒有原生章節/關注快捷鍵，這個腳本加：Ctrl+] 跳下一個標記點、Ctrl+[ 跳上一個標記點、G 關注目前影片的作者
// @match        https://www.douyin.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  function getSpots() {
    return Array.from(document.querySelectorAll('.xgplayer-spot[data-time]'))
      .map(el => parseFloat(el.getAttribute('data-time')))
      .filter(t => !Number.isNaN(t))
      .sort((a, b) => a - b);
  }

  function jump(direction) {
    const video = document.querySelector('video');
    if (!video) return;
    const spots = getSpots();
    if (!spots.length) return;
    const current = video.currentTime;
    let target;
    if (direction === 'next') {
      target = spots.find(t => t > current + 0.5);
    } else {
      target = [...spots].reverse().find(t => t < current - 0.5);
    }
    if (target !== undefined) {
      video.currentTime = target;
    }
  }

  function clickFollow() {
    // 影片右側動作列頭像上疊的紅色「+」關注角標，用穩定的 data-e2e 測試屬性定位（已關注時這個角標不存在，點了也沒事）
    const btn = document.querySelector('[data-e2e="feed-follow-icon"]');
    if (btn) btn.click();
  }

  document.addEventListener('keydown', (e) => {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) return;
    if (e.ctrlKey && !e.metaKey && !e.altKey) {
      if (e.key === ']') {
        jump('next');
        e.preventDefault();
      } else if (e.key === '[') {
        jump('prev');
        e.preventDefault();
      }
      return;
    }
    if (!e.ctrlKey && !e.metaKey && !e.altKey && (e.key === 'g' || e.key === 'G')) {
      clickFollow();
      e.preventDefault();
    }
  }, true);
})();
