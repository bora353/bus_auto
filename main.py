import os
import sys
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
import pytz

try:
    import requests
    import holidays
except ImportError:
    print("Required packages (requests, holidays) are missing.")
    sys.exit(1)

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY")

class TelegramBot:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        
    def send_message(self, text):
        if not TELEGRAM_BOT_TOKEN:
            return None
        try:
            resp = requests.post(f"{self.base_url}/sendMessage", data={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text
            })
            if resp.status_code == 200:
                return resp.json().get('result', {}).get('message_id')
        except Exception as e:
            print(f"Send Error: {e}")
        return None

    def edit_message(self, message_id, text):
        if not message_id or not TELEGRAM_BOT_TOKEN:
            return
        try:
            requests.post(f"{self.base_url}/editMessageText", data={
                'chat_id': TELEGRAM_CHAT_ID,
                'message_id': message_id,
                'text': text
            })
        except Exception as e:
            print(f"Edit Error: {e}")

    def delete_message(self, message_id):
        if not message_id or not TELEGRAM_BOT_TOKEN:
            return
        try:
            requests.post(f"{self.base_url}/deleteMessage", data={
                'chat_id': TELEGRAM_CHAT_ID,
                'message_id': message_id
            })
        except Exception as e:
            print(f"Delete Error: {e}")

    def check_stop_command(self, start_timestamp):
        if not TELEGRAM_BOT_TOKEN:
            return False
        try:
            resp = requests.get(f"{self.base_url}/getUpdates", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                for result in data.get('result', []):
                    msg = result.get('message', {})
                    text = msg.get('text', '').strip().lower()
                    date = msg.get('date', 0)
                    
                    # If message was sent after script started, and contains stop keywords
                    if date > start_timestamp and any(cmd in text for cmd in ["탑승", "중지", "종료", "끝", "stop"]):
                        return True
        except Exception as e:
            print(f"Update Error: {e}")
        return False

class BusAPI:
    def __init__(self):
        self.api_key = DATA_GO_KR_API_KEY
        
    def get_arrivals(self, station_id, target_routes):
        key = self.api_key if self.api_key else "1234567890"
        url = f"http://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalListv2?serviceKey={key}&stationId={station_id}&format=xml"
        
        results = []
        try:
            resp = requests.get(url, timeout=10)
            
            # If rate limited or error, use fallback test API
            if resp.status_code != 200:
                print(f"Primary API Error: {resp.status_code}. Using fallback...")
                url = f"http://openapi.gbis.go.kr/ws/rest/busarrivalservice/station?serviceKey=1234567890&stationId={station_id}"
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    return results
                    
            root = ET.fromstring(resp.text)
            for item in root.findall('.//busArrivalList'):
                r_id = item.find('routeId').text if item.find('routeId') is not None else ""
                if r_id in target_routes:
                    time1 = item.find('predictTime1').text
                    seat1 = item.find('remainSeatCnt1').text
                    results.append({
                        'route_name': target_routes[r_id],
                        'predict_time': int(time1) if time1 and time1.isdigit() else 999,
                        'remain_seat': seat1
                    })
        except Exception as e:
            print(f"Fetch Error: {e}")
        return results

def check_holiday():
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    
    # Check Weekend
    if now.weekday() >= 5: # 5=Sat, 6=Sun
        print("Today is weekend. Skipping.")
        sys.exit(0)
        
    # Check Korean Holiday
    kr_holidays = holidays.KR()
    if now.date() in kr_holidays:
        print(f"Today is holiday: {kr_holidays.get(now.date())}. Skipping.")
        sys.exit(0)

def run_morning_loop():
    print("Starting Morning Loop...")
    bot = TelegramBot()
    api = BusAPI()
    
    station_id = "233001219" # 36667 메타폴리스
    routes = {"233000426": "6012번"}
    
    # First fetch before sending the message to ensure the push notification contains real data
    arrivals = api.get_arrivals(station_id, routes)
    if arrivals:
        bus = arrivals[0]
        text = f"🚌 6012번 메타폴리스(중) {bus['predict_time']}분후 (💺{bus['remain_seat']}석)"
    else:
        text = "🌅 출근길 메타폴리스(중) - 현재 도착 예정 버스 없음"
        
    msg_id = bot.send_message(text)
    
    start_time = time.time()
    duration = 1 * 60 * 60 # 1 hour
    
    while time.time() - start_time < duration:
        # Wait 5 minutes
        time.sleep(300)
        
        arrivals = api.get_arrivals(station_id, routes)
        if arrivals:
            bus = arrivals[0]
            text = f"🚌 6012번 메타폴리스(중) {bus['predict_time']}분후 (💺{bus['remain_seat']}석)"
            if msg_id:
                bot.delete_message(msg_id)
            msg_id = bot.send_message(text)
        else:
            if msg_id:
                bot.delete_message(msg_id)
            msg_id = bot.send_message("🌅 출근길 메타폴리스(중) - 현재 도착 예정 버스 없음")
        
    if msg_id:
        bot.delete_message(msg_id)
        bot.send_message("✅ 오늘 아침 출근길 알림이 종료되었습니다. 화이팅!")

def run_evening_loop():
    print("Starting Evening Loop...")
    
    # 1. Absorb GitHub Cron Delay (Wait until 16:30 KST)
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    target_start = now.replace(hour=16, minute=30, second=0, microsecond=0)
    
    if now < target_start:
        wait_seconds = (target_start - now).total_seconds()
        print(f"Early start! Waiting for {wait_seconds} seconds until 16:30 KST...")
        time.sleep(wait_seconds)
        
    bot = TelegramBot()
    api = BusAPI()
    
    station_id = "206000539" # 07495 금토천교
    routes = {"233000266": "6003번", "233000426": "6012번"}
    
    # First fetch before sending the message to ensure the push notification contains real data
    arrivals = api.get_arrivals(station_id, routes)
    if arrivals:
        arrivals.sort(key=lambda x: x['predict_time'])
        fastest = arrivals[0]
        text = f"🏃 추천 버스: {fastest['route_name']} ({fastest['predict_time']}분 후 도착)\n\n"
        for bus in arrivals:
            mark = "👈 NOW" if bus == fastest else ""
            text += f"{bus['route_name']}: {bus['predict_time']}분 후 (잔여 {bus['remain_seat']}석) {mark}\n"
        text += "\n(알림을 끌 때는 '/종료' 또는 '/탑승' 이라고 쳐주세요!)"
    else:
        text = "🌇 퇴근길 정류장 - 현재 도착 예정 버스 없음\n(알림을 끌 때는 '/종료' 또는 '/탑승' 이라고 쳐주세요!)"
        
    msg_id = bot.send_message(text)
    
    start_time = time.time()
    duration = 1.5 * 60 * 60 # 1.5 hours
    
    while time.time() - start_time < duration:
        # Wait 5 minutes, but check for stop commands every 10 seconds
        for _ in range(30):
            if bot.check_stop_command(start_time):
                if msg_id:
                    bot.delete_message(msg_id)
                bot.send_message("✅ 버스 탑승을 확인했습니다! 오늘 퇴근길 알림을 조기 종료합니다. 푹 쉬세요!")
                return
            time.sleep(10)
        
        arrivals = api.get_arrivals(station_id, routes)
        if arrivals:
            arrivals.sort(key=lambda x: x['predict_time'])
            fastest = arrivals[0]
            text = f"🏃 추천 버스: {fastest['route_name']} ({fastest['predict_time']}분 후 도착)\n\n"
            for bus in arrivals:
                mark = "👈 NOW" if bus == fastest else ""
                text += f"{bus['route_name']}: {bus['predict_time']}분 후 (잔여 {bus['remain_seat']}석) {mark}\n"
            if msg_id:
                bot.delete_message(msg_id)
            msg_id = bot.send_message(text)
        else:
            if msg_id:
                bot.delete_message(msg_id)
            msg_id = bot.send_message("🌇 퇴근길 정류장 - 현재 도착 예정 버스 없음")
        
    if msg_id:
        bot.delete_message(msg_id)
        bot.send_message("✅ 오늘 퇴근길 알림이 종료되었습니다. 수고하셨습니다!")

def main():
    parser = argparse.ArgumentParser(description="Bus Notification System")
    parser.add_argument('--mode', required=True, choices=['morning', 'evening'], help="Run mode")
    args = parser.parse_args()
    
    check_holiday()
    
    if args.mode == 'morning':
        run_morning_loop()
    elif args.mode == 'evening':
        run_evening_loop()

if __name__ == "__main__":
    main()
