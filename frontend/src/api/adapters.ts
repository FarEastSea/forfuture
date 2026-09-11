type Media = {
  role?: string
  local_path?: string | null
  remote_url?: string | null
  status?: string
  duration_seconds?: number | null
}

type ContentItem = {
  id: number
  platform: string
  platform_item_id: string
  target_account_id?: number
  author?: { platform_uid?: string | null; name?: string | null; avatar?: string | null }
  title?: string | null
  body?: string | null
  posted_at?: string | null
  ip_location?: string | null
  geo_location?: string | null
  device?: string | null
  source_url?: string | null
  metrics?: Record<string, number>
  extra?: Record<string, any>
  media?: Media[]
  comments?: any[]
  comment_total?: number
}

function imagesOf(item: ContentItem) {
  return (item.media || [])
    .filter((m) => m.role === 'image' || m.role === 'cover')
    .map((m) => ({
      local_path: m.local_path || '',
      url: m.remote_url || '',
    }))
}

function videoOf(item: ContentItem) {
  return (item.media || []).find((m) => m.role === 'video')
}

export function toQqPost(item: ContentItem) {
  const video = videoOf(item)
  return {
    id: item.id,
    author_qq: item.author?.platform_uid,
    qq_number: item.author?.platform_uid,
    author_nickname: item.author?.name,
    author_avatar: item.author?.avatar,
    content: item.body,
    post_time: item.posted_at,
    device_info: item.device,
    location: item.geo_location,
    like_count: item.metrics?.like || 0,
    comment_count: item.metrics?.comment || item.comment_total || 0,
    forward_content: item.extra?.forward_content,
    images: imagesOf(item),
    local_video_path: video?.local_path || '',
    video_url: video?.remote_url || '',
    comments: (item.comments || []).map((c) => ({
      id: c.id,
      author_nickname: c.author_nickname || c.author?.name,
      content: c.content || c.body,
      comment_time: c.comment_time || c.commented_at,
    })),
  }
}

export function toXhsNote(item: ContentItem) {
  const video = videoOf(item)
  return {
    id: item.id,
    title: item.title,
    content: item.body,
    post_time: item.posted_at,
    author_nickname: item.author?.name,
    author_avatar: item.author?.avatar,
    like_count: item.metrics?.like || 0,
    collect_count: item.metrics?.collect || 0,
    comment_count: item.metrics?.comment || item.comment_total || 0,
    share_count: item.metrics?.share || 0,
    ip_location: item.ip_location,
    location: item.geo_location,
    device_info: item.device,
    note_type: item.extra?.note_type,
    tags: item.extra?.tags || [],
    at_user_list: item.extra?.at_users || [],
    images: imagesOf(item),
    local_video_path: video?.local_path || '',
    video_url: video?.remote_url || '',
    video_duration: video?.duration_seconds,
    comments: mapXhsComments(item.comments || []),
  }
}

function mapXhsComments(comments: any[]): any[] {
  if (!comments.length) {
    return []
  }
  if (comments[0]?.replies || comments[0]?.author) {
    return comments.map((c) => ({
      id: c.id,
      author_nickname: c.author?.name || c.author_nickname,
      author_avatar: c.author?.avatar || c.author_avatar,
      content: c.body || c.content,
      comment_time: c.commented_at || c.comment_time,
      like_count: c.like_count,
      ip_location: c.ip_location,
      is_author: c.is_author_reply || c.is_author,
      target_nickname: c.reply_to_name,
      replies: mapXhsComments(c.replies || []),
    }))
  }
  const roots = comments.filter((c) => !c.parent_id)
  const children = comments.filter((c) => c.parent_id)
  return roots.map((c) => ({
    ...flattenComment(c),
    replies: children.filter((r) => r.parent_id === c.id).map(flattenComment),
  }))
}

function flattenComment(c: any) {
  return {
    id: c.id,
    author_nickname: c.author_nickname || c.author?.name,
    author_avatar: c.author_avatar || c.author?.avatar,
    content: c.content || c.body,
    comment_time: c.comment_time || c.commented_at,
    like_count: c.like_count,
    ip_location: c.ip_location,
    is_author: c.is_author || c.is_author_reply,
    target_nickname: c.reply_to_name || c.target_nickname,
  }
}
