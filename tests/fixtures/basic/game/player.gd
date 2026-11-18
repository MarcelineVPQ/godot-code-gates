extends Node2D
class_name Player

signal moved(to: Vector2)

var trail: Array = []


func _process(delta: float) -> void:
	position.x += delta
	_update_trail()
	moved.emit(position)


func _update_trail() -> void:
	for p in trail:
		p.age += 1


func on_click() -> void:
	# Runs once per click, so its loop is not hot.
	for i in range(3):
		print(i)
