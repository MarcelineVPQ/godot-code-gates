extends Control

@export var player: Player


func _ready() -> void:
	player.moved.connect(_on_moved)


func _on_moved(to: Vector2) -> void:
	for label in get_children():
		label.text = str(to)
