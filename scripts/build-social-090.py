import zipfile, pathlib, shutil, re, os
ROOT=pathlib.Path("work"); ZIP=pathlib.Path("tmp/jesusschmuck-social-mcp-0.8.2-live.zip"); OUT=pathlib.Path("dist/jesusschmuck-social-mcp-0.9.0.zip")
if ROOT.exists(): shutil.rmtree(ROOT)
if OUT.parent.exists(): shutil.rmtree(OUT.parent)
ROOT.mkdir(parents=True); OUT.parent.mkdir(parents=True)
with zipfile.ZipFile(ZIP) as z: z.extractall(ROOT)
tops=[p for p in ROOT.iterdir() if p.is_dir()]
if len(tops)!=1: raise SystemExit("unexpected archive layout")
SRC=tops[0]
def read(rel): return (SRC/rel).read_text(encoding="utf-8")
def write(rel,s):
    p=SRC/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(s,encoding="utf-8")
def rep(rel,old,new):
    s=read(rel)
    if old not in s: raise SystemExit("missing patch anchor "+rel)
    write(rel,s.replace(old,new,1))

rep("jesusschmuck-social-mcp.php","Version: 0.8.2","Version: 0.9.0")
rep("jesusschmuck-social-mcp.php","define('JSSOCIAL_VERSION', '0.8.2');","define('JSSOCIAL_VERSION', '0.9.0');")
rep("jesusschmuck-social-mcp.php","'JSSocial_TikTok_Actions' => 'tiktok-actions',","'JSSocial_TikTok_Actions' => 'tiktok-actions',\n        'JSSocial_TikTok_Direct' => 'tiktok-direct',")
rep("includes/class-tiktok.php","private const SCOPE = 'user.info.basic,video.upload';","private const SCOPE = 'user.info.basic,video.upload,video.publish';\n    private const BASE_SCOPE = 'user.info.basic,video.upload';")
rep("includes/class-tiktok.php","foreach (explode(',', self::SCOPE) as $scope) { if (!in_array($scope, $scopes, true)) { throw new RuntimeException('TikTok-Konto erneut verbinden und Profil- sowie Upload-Berechtigung erteilen.'); } }","foreach (explode(',', self::BASE_SCOPE) as $scope) { if (!in_array($scope, $scopes, true)) { throw new RuntimeException('TikTok-Konto erneut verbinden und Profil- sowie Upload-Berechtigung erteilen.'); } }")
rep("includes/class-tiktok.php","'features'=>['oauth','account_verify','inbox_upload','upload_status']","'features'=>['oauth','account_verify','inbox_upload','direct_post','scheduled_direct_post','upload_status']")
rep("includes/class-tiktok.php","$out['upload_enabled'] = $out['connected'] && in_array('video.upload',explode(',',$c['scope']??''),true);","$out['upload_enabled'] = $out['connected'] && in_array('video.upload',explode(',',$c['scope']??''),true);\n            $out['direct_post_enabled'] = $out['connected'] && in_array('video.publish',explode(',',$c['scope']??''),true);")
rep("includes/class-tiktok.php","$out['note'] = 'Sandbox: Videoübergabe nach Vorschau/Freigabe. Beschreibung separat kopieren; Veröffentlichung manuell in TikTok. Keine Terminplanung.';","$out['note'] = $out['direct_post_enabled'] ? 'TikTok: Postfach-Upload und Direct Post verfügbar. Direct-Post-Termine werden vom Jesusschmuck-Server-Worker ausgelöst.' : 'TikTok: Postfach-Upload verfügbar. Für Direct Post Konto neu verbinden und video.publish erlauben.';")

direct=r'''<?php
defined('ABSPATH') || exit;
final class JSSocial_TikTok_Direct {
    private static function access(): array {
        JSSocial_TikTok::verify(); $c=JSSocial_Config::tiktok_connection();
        if (!in_array('video.publish',explode(',',$c['scope']??''),true)) throw new RuntimeException('TikTok video.publish fehlt. TikTok unter Jesusschmuck Social neu verbinden.');
        return $c;
    }
    private static function api(array $c,string $path,array $body=[]): array {
        $r=wp_remote_post('https://open.tiktokapis.com/v2/post/publish/'.$path,['timeout'=>20,'redirection'=>0,'headers'=>['Authorization'=>'Bearer '.$c['access_token'],'Content-Type'=>'application/json; charset=UTF-8'],'body'=>wp_json_encode($body)]);
        if(is_wp_error($r)) throw new RuntimeException('TikTok ist derzeit nicht erreichbar. Status prüfen; nicht blind wiederholen.');
        $status=wp_remote_retrieve_response_code($r); $d=json_decode(wp_remote_retrieve_body($r),true); $code=is_array($d)?(string)($d['error']['code']??''):'';
        if($status<200||$status>=300||!is_array($d)||$code!=='ok'||!is_array($d['data']??null)) throw new RuntimeException('TikTok-Anfrage nicht bestätigt (HTTP '.(int)$status.($code?', '.$code:'').').');
        return $d['data'];
    }
    private static function asset(int $id): array {
        $x=JSSocial_Service::asset($id,'reel',[]);
        if($x['bytes']<=0||$x['bytes']>12*1024*1024||min($x['width'],$x['height'])<360||max($x['width'],$x['height'])>4096) throw new InvalidArgumentException('TikTok Direct Post: MP4 bis 12 MB, 3–900 Sekunden und 360–4096 Pixel je Seite.');
        return $x;
    }
    private static function creator(array $c): array {
        $d=self::api($c,'creator_info/query/',[]);
        $privacy=array_values(array_intersect((array)($d['privacy_level_options']??[]),['PUBLIC_TO_EVERYONE','MUTUAL_FOLLOW_FRIENDS','FOLLOWER_OF_CREATOR','SELF_ONLY']));
        $max=(int)($d['max_video_post_duration_sec']??0);
        if(!$privacy||$max<3) throw new RuntimeException('TikTok hat keine vollständigen Creator-Info-Daten geliefert.');
        return ['username'=>sanitize_text_field((string)($d['creator_username']??'')),'nickname'=>sanitize_text_field((string)($d['creator_nickname']??'')),'privacy_level_options'=>$privacy,'comment_disabled'=>(bool)($d['comment_disabled']??true),'duet_disabled'=>(bool)($d['duet_disabled']??true),'stitch_disabled'=>(bool)($d['stitch_disabled']??true),'max_video_post_duration_sec'=>$max,'checked_at'=>gmdate('c')];
    }
    public static function creator_info(): array { $c=self::access(); return ['account'=>$c['account'],'creator_info'=>self::creator($c)]; }
    private static function require_job(array $j): void { if(($j['payload']['channel']??'')!=='tiktok_direct') throw new InvalidArgumentException('Kein TikTok-Direct-Post-Auftrag.'); }
    private static function current_asset(array $j): array { $old=$j['payload']['asset']; $now=self::asset((int)$old['attachment_id']); if($now['sha256']!==$old['sha256']||$now['bytes']!==$old['bytes']) throw new RuntimeException('TikTok-Videodatei seit Vorschau verändert. Neue Freigabe erforderlich.'); return $now; }
    private static function same_account(array $j,array $c): void { if(($j['payload']['account']['id']??'')!==($c['account']['id']??'')) throw new RuntimeException('TikTok-Zielkonto wurde geändert. Neue Vorschau erforderlich.'); }
    private static function compatible(array $j,array $creator): void {
        $p=$j['payload'];$o=$p['options'];
        if(!in_array($p['privacy_level'],$creator['privacy_level_options'],true)) throw new InvalidArgumentException('Gewählte TikTok-Sichtbarkeit ist nicht mehr verfügbar.');
        if((float)$p['asset']['seconds']>(int)$creator['max_video_post_duration_sec']) throw new InvalidArgumentException('TikTok erlaubt aktuell eine kürzere Videodauer.');
        if($creator['comment_disabled']&&!$o['disable_comment']) throw new InvalidArgumentException('Kommentare sind für dieses Konto inzwischen deaktiviert.');
        if($creator['duet_disabled']&&!$o['disable_duet']) throw new InvalidArgumentException('Duette sind für dieses Konto inzwischen deaktiviert.');
        if($creator['stitch_disabled']&&!$o['disable_stitch']) throw new InvalidArgumentException('Stitch ist für dieses Konto inzwischen deaktiviert.');
    }
    public static function prepare(array $a): array {
        return JSSocial_Store::lock(function()use($a){
            $key=(string)($a['request_key']??''); if(!preg_match('/^[A-Za-z0-9_-]{8,64}$/D',$key)) throw new InvalidArgumentException('request_key mit 8–64 Zeichen angeben.');
            $request_hash=hash('sha256',wp_json_encode($a)); $old=JSSocial_Store::by_key($key);
            if($old){self::require_job($old);if(($old['request_hash']??'')!==$request_hash)throw new InvalidArgumentException('request_key bereits mit anderen Daten verwendet.');return self::preview($old);}
            if(($a['rights_confirmed']??false)!==true||mb_strlen(trim((string)($a['rights_basis']??'')))<8) throw new InvalidArgumentException('Video-/Audiorechte und konkrete Rechtebasis bestätigen.');
            $title=trim((string)($a['title']??'')); if($title===''||strlen($title)>8800) throw new InvalidArgumentException('TikTok-Caption fehlt oder ist zu lang.');
            if(function_exists('mb_convert_encoding')&&intdiv(strlen(mb_convert_encoding($title,'UTF-16LE','UTF-8')),2)>2200) throw new InvalidArgumentException('TikTok-Caption überschreitet 2200 UTF-16-Zeichen.');
            $c=self::access();$creator=self::creator($c);$asset=self::asset((int)$a['attachment_id']);
            if((float)$asset['seconds']>$creator['max_video_post_duration_sec']) throw new InvalidArgumentException('Video ist länger als TikToks aktuelles Kontolimit.');
            $privacy=(string)($a['privacy_level']??''); if(!in_array($privacy,$creator['privacy_level_options'],true)) throw new InvalidArgumentException('Gewählte TikTok-Sichtbarkeit ist nicht verfügbar.');
            $due=JSSocial_Service::time((string)($a['local_datetime']??''),(string)($a['utc_offset']??'')); if($due&&$due<=time()) throw new InvalidArgumentException('TikTok-Termin muss in der Zukunft liegen.');
            $cover=(int)($a['video_cover_timestamp_ms']??0); if($cover<0||($cover&&$cover>(int)ceil((float)$asset['seconds']*1000))) throw new InvalidArgumentException('Cover-Zeitpunkt liegt außerhalb des Videos.');
            $options=['disable_comment'=>$creator['comment_disabled']?true:(bool)($a['disable_comment']??false),'disable_duet'=>$creator['duet_disabled']?true:(bool)($a['disable_duet']??false),'disable_stitch'=>$creator['stitch_disabled']?true:(bool)($a['disable_stitch']??false),'video_cover_timestamp_ms'=>$cover,'brand_content_toggle'=>(bool)($a['brand_content_toggle']??false),'brand_organic_toggle'=>(bool)($a['brand_organic_toggle']??false),'is_aigc'=>(bool)($a['is_aigc']??false)];
            $payload=['channel'=>'tiktok_direct','account'=>$c['account'],'creator_info'=>$creator,'asset'=>$asset,'title'=>$title,'privacy_level'=>$privacy,'options'=>$options,'rights_basis'=>trim((string)$a['rights_basis']),'due'=>$due,'local_datetime'=>(string)($a['local_datetime']??''),'utc_offset'=>(string)($a['utc_offset']??''),'timezone'=>'Europe/Berlin'];
            $job=['request_key'=>$key,'request_hash'=>$request_hash,'revision'=>1,'state'=>'draft','due'=>$due,'payload'=>$payload,'results'=>['tiktok'=>['state'=>'pending','stage'=>'new','publish_id'=>'','error'=>'','next_at'=>0,'init_sent'=>false,'transfer_attempted'=>false,'read_failures'=>0]],'approval'=>null,'audit'=>[]];
            JSSocial_Store::event($job,'TikTok-Direct-Post-Entwurf erstellt. Noch keine Veröffentlichung/Planung.'); return self::preview(JSSocial_Store::insert($job));
        });
    }
    public static function preview(array $j): array {
        self::require_job($j);$hash=JSSocial_Service::hash($j);
        return ['id'=>$j['id'],'state'=>$j['state'],'preview_hash'=>$hash,'approval_text'=>'FREIGEBEN TIKTOK '.$j['id'].' '.$hash,'when_berlin'=>JSSocial_Service::local((int)$j['due']),'account'=>$j['payload']['account'],'creator_info'=>$j['payload']['creator_info'],'asset'=>$j['payload']['asset'],'title'=>$j['payload']['title'],'privacy_level'=>$j['payload']['privacy_level'],'options'=>$j['payload']['options'],'rights_basis'=>$j['payload']['rights_basis'],'result'=>$j['results']['tiktok'],'audit'=>$j['audit'],'warnings'=>['Creator Info wird unmittelbar vor Ausführung erneut geprüft.','Nicht auditierte TikTok-Clients können nur privat posten.','Termin wird vom Jesusschmuck-Server-Worker ausgelöst.']];
    }
    public static function approve(int $id,string $hash,string $confirmation,string $evidence): array {
        return JSSocial_Store::lock(function()use($id,$hash,$confirmation,$evidence){
            $j=JSSocial_Store::get($id);self::require_job($j);if(!hash_equals(JSSocial_Service::hash($j),$hash))throw new InvalidArgumentException('TikTok-Vorschau veraltet.');
            if($confirmation!=='FREIGEBEN TIKTOK '.$id.' '.$hash||mb_strlen(trim($evidence))<5)throw new InvalidArgumentException('Ausdrückliche Freigabe genau dieser Vorschau erforderlich.');
            if($j['approval']&&hash_equals($j['approval']['hash']??'',$hash))return self::preview($j);if($j['state']!=='draft')throw new InvalidArgumentException('Nur Entwürfe können freigegeben werden.');
            if(!JSSocial_Worker::health()['healthy'])throw new RuntimeException('Server-Worker noch nicht zuverlässig nachgewiesen.');if($j['due']&&$j['due']<=time())throw new InvalidArgumentException('Termin ist inzwischen vergangen.');
            self::current_asset($j);$c=self::access();self::same_account($j,$c);self::compatible($j,self::creator($c));
            $j['approval']=['hash'=>$hash,'at'=>gmdate('c'),'evidence'=>sanitize_textarea_field($evidence),'actor'=>get_current_user_id()?:'mcp'];$j['state']='queued';$j['results']['tiktok']['state']='pending';
            JSSocial_Store::event($j,'TikTok Direct Post ausdrücklich freigegeben: '.JSSocial_Service::local((int)$j['due']));JSSocial_Store::save($j);return self::preview($j);
        });
    }
    public static function reschedule(int $id,string $hash,string $local,string $offset=''): array { return JSSocial_Store::lock(function()use($id,$hash,$local,$offset){$j=JSSocial_Store::get($id);self::require_job($j);if(!hash_equals(JSSocial_Service::hash($j),$hash))throw new InvalidArgumentException('TikTok-Vorschau veraltet.');$r=$j['results']['tiktok'];if(!in_array($j['state'],['draft','queued','cancelled'],true)||($r['stage']??'new')!=='new'||!empty($r['init_sent']))throw new InvalidArgumentException('Gestarteter TikTok-Post kann nicht verschoben werden.');$due=JSSocial_Service::time($local,$offset);if($due&&$due<=time())throw new InvalidArgumentException('Termin liegt in der Vergangenheit.');$j['due']=$due;$j['payload']['due']=$due;$j['payload']['local_datetime']=$local;$j['payload']['utc_offset']=$offset;$j['revision']++;$j['state']='draft';$j['approval']=null;$j['results']['tiktok']['state']='pending';$j['results']['tiktok']['next_at']=0;JSSocial_Store::event($j,'TikTok-Termin geändert. Neue Freigabe erforderlich.');JSSocial_Store::save($j);return self::preview($j);}); }
    public static function cancel(int $id,string $hash): array { return JSSocial_Store::lock(function()use($id,$hash){$j=JSSocial_Store::get($id);self::require_job($j);if(!hash_equals(JSSocial_Service::hash($j),$hash))throw new InvalidArgumentException('TikTok-Vorschau veraltet.');if(!empty($j['results']['tiktok']['init_sent']))throw new InvalidArgumentException('TikTok-Initialisierung bereits gesendet; nicht blind stornieren.');$j['state']='cancelled';$j['results']['tiktok']['state']='failed';$j['results']['tiktok']['error']='Vor TikTok-Initialisierung storniert.';JSSocial_Store::event($j,'TikTok Direct Post storniert.');JSSocial_Store::save($j);return self::preview($j);}); }
    private static function target(string $url): void {$u=wp_parse_url($url);if(!$u||($u['scheme']??'')!=='https'||!in_array($u['host']??'',['open-upload.tiktokapis.com','upload.us.tiktokapis.com','open-upload-i18n.tiktokapis.com'],true)||isset($u['user'])||isset($u['pass'])||isset($u['port']))throw new RuntimeException('TikTok-Uploadziel nicht freigegeben.');}
    private static function bytes(array $j): string {$a=self::current_asset($j);$b=file_get_contents(get_attached_file($a['attachment_id']));if($b===false||strlen($b)!==$a['bytes']||hash('sha256',$b)!==$a['sha256'])throw new RuntimeException('Videodatei konnte nicht unverändert gelesen werden.');return $b;}
    private static function post_info(array $j): array {$p=$j['payload'];$o=$p['options'];$x=['title'=>$p['title'],'privacy_level'=>$p['privacy_level'],'disable_duet'=>$o['disable_duet'],'disable_comment'=>$o['disable_comment'],'disable_stitch'=>$o['disable_stitch'],'brand_content_toggle'=>$o['brand_content_toggle'],'brand_organic_toggle'=>$o['brand_organic_toggle']];if($o['video_cover_timestamp_ms'])$x['video_cover_timestamp_ms']=$o['video_cover_timestamp_ms'];if($o['is_aigc'])$x['is_aigc']=true;return $x;}
    private static function update_status(array &$j,array $c): void {$r=&$j['results']['tiktok'];if(empty($r['publish_id']))return;try{$d=self::api($c,'status/fetch/',['publish_id'=>$r['publish_id']]);}catch(Throwable $e){$r['read_failures']++;$r['error']=$e->getMessage();$r['state']='verifying';$r['next_at']=time()+120;$j['state']='running';return;}$s=(string)($d['status']??'');$r['result']=['provider_status'=>$s,'checked_at'=>gmdate('c')];if($s==='PUBLISH_COMPLETE'){$r['state']='published';$r['stage']='complete';$r['next_at']=0;$r['error']='';$j['state']='published';JSSocial_Store::event($j,'TikTok bestätigt PUBLISH_COMPLETE.');}elseif($s==='FAILED'){$reason=preg_match('/^[a-z0-9_]{1,120}$/D',(string)($d['fail_reason']??''))?(string)$d['fail_reason']:'unknown';$r['state']='failed';$r['stage']='failed';$r['next_at']=0;$r['error']='TikTok fehlgeschlagen: '.$reason;$j['state']='failed';}elseif(in_array($s,['PROCESSING_UPLOAD','PROCESSING_DOWNLOAD'],true)){$r['state']='verifying';$r['stage']='status';$r['next_at']=time()+60;$j['state']='running';}else{$r['state']='unknown';$r['next_at']=0;$r['error']='Unbekannter TikTok-Status. Keine automatische Wiederholung.';$j['state']='unknown';}}
    public static function worker(array &$j): void {self::require_job($j);$r=&$j['results']['tiktok'];if(!$j['approval']||!hash_equals($j['approval']['hash']??'',JSSocial_Service::hash($j))){$j['state']='held';$r['state']='failed';$r['error']='TikTok-Freigabe fehlt.';JSSocial_Store::save($j);return;}if($j['due']&&$j['due']>time())return;$c=self::access();self::same_account($j,$c);if(!empty($r['publish_id'])&&(!empty($r['transfer_attempted'])||($r['stage']??'')==='status')){self::update_status($j,$c);JSSocial_Store::save($j);return;}if(($r['stage']??'new')==='new'){try{self::compatible($j,self::creator($c));}catch(InvalidArgumentException $e){$j['state']='held';$r['state']='failed';$r['error']=$e->getMessage();JSSocial_Store::save($j);return;}catch(Throwable $e){$j['state']='running';$r['state']='pending';$r['error']=$e->getMessage();$r['next_at']=time()+120;JSSocial_Store::save($j);return;}$bytes=self::bytes($j);$asset=$j['payload']['asset'];$r['init_sent']=true;$r['stage']='init';$r['state']='verifying';$j['state']='running';JSSocial_Store::save($j);try{$d=self::api($c,'video/init/',['post_info'=>self::post_info($j),'source_info'=>['source'=>'FILE_UPLOAD','video_size'=>$asset['bytes'],'chunk_size'=>$asset['bytes'],'total_chunk_count'=>1]]);}catch(Throwable $e){$r['state']='unknown';$r['stage']='init_unknown';$r['error']=$e->getMessage().' Initialisierung nicht blind wiederholen.';$j['state']='unknown';JSSocial_Store::save($j);return;}$pid=$d['publish_id']??'';$url=$d['upload_url']??'';if(!is_string($pid)||$pid===''||!is_string($url)||$url===''){$r['state']='unknown';$r['error']='Unvollständige TikTok-Initialisierung.';$j['state']='unknown';JSSocial_Store::save($j);return;}self::target($url);$r['publish_id']=$pid;$r['upload_url']=$url;$r['stage']='upload';JSSocial_Store::save($j);}if(($r['stage']??'')==='upload'&&!$r['transfer_attempted']){$bytes=self::bytes($j);$asset=$j['payload']['asset'];$url=(string)$r['upload_url'];self::target($url);$r['transfer_attempted']=true;JSSocial_Store::save($j);$resp=wp_remote_request($url,['method'=>'PUT','timeout'=>25,'redirection'=>0,'headers'=>['Content-Type'=>'video/mp4','Content-Length'=>(string)$asset['bytes'],'Content-Range'=>'bytes 0-'.($asset['bytes']-1).'/'.$asset['bytes']],'body'=>$bytes]);if(is_wp_error($resp)||wp_remote_retrieve_response_code($resp)!==201){$r['state']='unknown';$r['stage']='upload_unknown';$r['error']='Dateiübertragung nicht eindeutig bestätigt; nicht neu senden.';$j['state']='unknown';JSSocial_Store::save($j);return;}unset($r['upload_url']);$r['stage']='status';$r['state']='verifying';$r['next_at']=time()+60;$j['state']='running';JSSocial_Store::save($j);}}
    public static function status(int $id): array {return JSSocial_Store::lock(function()use($id){$j=JSSocial_Store::get($id);self::require_job($j);if(!empty($j['results']['tiktok']['publish_id'])){$c=self::access();self::same_account($j,$c);self::update_status($j,$c);JSSocial_Store::save($j);}return self::preview($j);});}
}
'''
write("includes/class-tiktok-direct.php",direct)

mcp=read("includes/class-mcp.php"); anchor="        return array_map(function($r){"
defs=r'''        $rows[]=['js_social_tiktok_creator_info','Liest TikToks aktuelle Creator-Info für Direct Post. Benötigt video.publish; keine Veröffentlichung.',self::obj([]),true];
        $rows[]=['js_social_tiktok_direct_prepare','Erstellt unverbindliche TikTok-Direct-Post-Vorschau mit Caption, Sichtbarkeit, Interaktionen, Commercial-Content-Angaben, Cover und optionalem Europe/Berlin-Termin.',self::obj(['request_key'=>['type'=>'string','pattern'=>'^[A-Za-z0-9_-]{8,64}$'],'attachment_id'=>$int,'title'=>$str,'privacy_level'=>['type'=>'string','enum'=>['PUBLIC_TO_EVERYONE','MUTUAL_FOLLOW_FRIENDS','FOLLOWER_OF_CREATOR','SELF_ONLY']],'disable_comment'=>['type'=>'boolean'],'disable_duet'=>['type'=>'boolean'],'disable_stitch'=>['type'=>'boolean'],'video_cover_timestamp_ms'=>['type'=>'integer','minimum'=>0],'brand_content_toggle'=>['type'=>'boolean'],'brand_organic_toggle'=>['type'=>'boolean'],'is_aigc'=>['type'=>'boolean'],'rights_confirmed'=>['type'=>'boolean'],'rights_basis'=>$str,'local_datetime'=>['type'=>'string','pattern'=>'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$'],'utc_offset'=>['type'=>'string','enum'=>['+01:00','+02:00']]],['request_key','attachment_id','title','privacy_level','brand_content_toggle','brand_organic_toggle','rights_confirmed','rights_basis']),false];
        $rows[]=['js_social_tiktok_direct_preview','Liest gespeicherte TikTok-Direct-Post-Vorschau.',self::obj($id,['job_id']),true];
        $rows[]=['js_social_tiktok_direct_approve','VERÖFFENTLICHUNG/VERBINDLICHE PLANUNG AUF TIKTOK: nur nach ausdrücklicher Freigabe der vollständig gezeigten Vorschau.',self::obj($hash+['confirmation'=>$str,'approval_evidence'=>$str],['job_id','preview_hash','confirmation','approval_evidence']),false];
        $rows[]=['js_social_tiktok_direct_reschedule','Ändert Termin eines noch nicht initialisierten TikTok Direct Posts; neue Freigabe erforderlich.',self::obj($hash+['local_datetime'=>$str,'utc_offset'=>['type'=>'string','enum'=>['','+01:00','+02:00']]],['job_id','preview_hash','local_datetime']),false];
        $rows[]=['js_social_tiktok_direct_cancel','Storniert TikTok Direct Post vor Provider-Initialisierung.',self::obj($hash,['job_id','preview_hash']),false];
        $rows[]=['js_social_tiktok_direct_status','Prüft TikTok-Status anhand gespeicherter publish_id; startet keinen neuen Post.',self::obj($id,['job_id']),true];
'''
if anchor not in mcp: raise SystemExit("mcp definitions anchor missing")
mcp=mcp.replace(anchor,defs+anchor,1)
dispatch="'js_social_tiktok_status' => JSSocial_TikTok_Actions::status($a['operation_id']),"
insert=dispatch+"""
            'js_social_tiktok_creator_info' => JSSocial_TikTok_Direct::creator_info(),
            'js_social_tiktok_direct_prepare' => JSSocial_TikTok_Direct::prepare($a),
            'js_social_tiktok_direct_preview' => JSSocial_TikTok_Direct::preview(JSSocial_Store::get($a['job_id'])),
            'js_social_tiktok_direct_approve' => JSSocial_TikTok_Direct::approve($a['job_id'],$a['preview_hash'],$a['confirmation'],$a['approval_evidence']),
            'js_social_tiktok_direct_reschedule' => JSSocial_TikTok_Direct::reschedule($a['job_id'],$a['preview_hash'],$a['local_datetime'],$a['utc_offset']??''),
            'js_social_tiktok_direct_cancel' => JSSocial_TikTok_Direct::cancel($a['job_id'],$a['preview_hash']),
            'js_social_tiktok_direct_status' => JSSocial_TikTok_Direct::status($a['job_id']),"""
if dispatch not in mcp: raise SystemExit("mcp dispatch anchor missing")
write("includes/class-mcp.php",mcp.replace(dispatch,insert,1))
rep("includes/class-worker.php","                $j = JSSocial_Store::get($id);\n                if (!$j['approval']","                $j = JSSocial_Store::get($id);\n                if (($j['payload']['channel'] ?? '') === 'tiktok_direct') { JSSocial_TikTok_Direct::worker($j); $count++; continue; }\n                if (!$j['approval']")
write("TIKTOK.md","""# TikTok Content Posting 0.9.0

Unterstützt weiterhin Upload-to-TikTok (video.upload) und zusätzlich Direct Post (video.publish).

Direct Post: Creator Info wird vor Vorschau und vor Ausführung erneut geprüft. Der vorhandene Jesusschmuck-Server-Worker löst freigegebene Posts erst zum gespeicherten Europe/Berlin-Termin aus. TikTok selbst erhält keinen Vorab-Termin. Nicht eindeutige Initialisierungen oder Uploads werden nie blind wiederholt. Nicht auditierte TikTok-Clients können nur privat posten.
""")
s=read("readme.txt"); s=re.sub(r"Stable tag:\s*0\.8\.2","Stable tag: 0.9.0",s); write("readme.txt",s)
for f in ["jesusschmuck-social-mcp.php","includes/class-tiktok.php","includes/class-tiktok-direct.php","includes/class-mcp.php","includes/class-worker.php"]:
    rc=os.system(f"php -l {SRC/f} >/tmp/lint.txt 2>&1")
    if rc: raise SystemExit(pathlib.Path("/tmp/lint.txt").read_text())
with zipfile.ZipFile(OUT,"w",zipfile.ZIP_DEFLATED) as z:
    for p in SRC.rglob("*"):
        if p.is_file(): z.write(p,pathlib.Path("jesusschmuck-social-mcp")/p.relative_to(SRC))
print(OUT,OUT.stat().st_size)
